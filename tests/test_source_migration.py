import hashlib
import json
from importlib.resources import files
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.main import create_app
from backend.schemas.models import SOURCES, QueryStrand, SearchStrategy, StoredBrief
from backend.schemas.source_registry import PreviousSourceCategory
from backend.services import source_registry
from backend.services.ranking import score_reference
from backend.services.source_policy import SourcePolicy, publishing_platform
from config import SOURCE_CATEGORY_RENAMES
from tests.conftest import completed_run
from tests.test_discovery import discovery_api as discovery_api
from tests.test_discovery import execute, live_settings
from tests.test_ranking import reference
from tests.test_source_registry import EXPECTED_CATEGORIES


@pytest.mark.parametrize("old,new", SOURCE_CATEGORY_RENAMES.items())
def test_renamed_category_submissions_normalize_and_keep_scope(client, old, new):
    brief = client.post("/briefs", json={"concept": "A film", "preferred_sources": [old, new]}).json()
    assert brief["preferred_sources"] == [new]
    archived_strategy = strategy([old], [old])
    assert archived_strategy.preferred_sources == archived_strategy.source_priority == [old]
    jobs = SourcePolicy().jobs(archived_strategy, 1)
    assert jobs[0].categories == [new]
    assert jobs[0].domains == EXPECTED_CATEGORIES[new][1]
    with pytest.raises(ValidationError):
        strategy([new], [old, new])


def test_planning_schema_only_advertises_current_category_names():
    from typing import get_args

    from backend.schemas.models import StrategyDraft

    assert set(get_args(PreviousSourceCategory)) == set(SOURCE_CATEGORY_RENAMES)
    assert StrategyDraft.model_json_schema()["properties"]["source_priority"]["items"]["enum"] == SOURCES


@pytest.mark.parametrize("old,new", SOURCE_CATEGORY_RENAMES.items())
def test_saved_research_with_previous_category_names_reads_and_exports_without_rewriting(client, old, new):
    run, _ = completed_run(client, count=4)
    selected = [item["id"] for item in reversed(run["results"])]
    client.put(
        f"/search-runs/{run['id']}/selection", json={"ordered_ids": selected, "expected_version": 0}
    ).raise_for_status()
    repository = client.app.state.workflow.repository
    stored = repository.get("run", run["id"])
    brief = repository.get("brief", run["brief_id"])
    brief["preferred_sources"] = [old]
    stored["strategy"]["preferred_sources"] = stored["strategy"]["source_priority"] = [old]
    stored["coverage"] = [{"category": old, "queries_attempted": 1, "references_returned": 4, "note": ""}]
    for item in stored["results"]:
        item["source_category"] = old
    with repository.connection() as connection:
        connection.execute(
            "UPDATE records SET payload=? WHERE kind='run' AND id=?", (json.dumps(stored), run["id"])
        )
        connection.execute(
            "UPDATE records SET payload=? WHERE kind='brief' AND id=?", (json.dumps(brief), run["brief_id"])
        )
    restored = client.get(f"/search-runs/{run['id']}")
    assert restored.status_code == 200
    assert restored.json()["strategy"]["source_priority"] == [old]
    assert all(item["source_category"] == old for item in restored.json()["results"])
    assert client.get(f"/briefs/{run['brief_id']}").json()["preferred_sources"] == [old]
    assert restored.json()["selection"]["ordered_ids"] == selected
    assert (
        client.post(
            f"/search-runs/{run['id']}/exports/pptx", json={"expected_selection_version": 1}
        ).status_code
        == 201
    )
    assert repository.get("run", run["id"]) == stored
    assert repository.get("brief", run["brief_id"]) == brief


@pytest.mark.parametrize("old,new", SOURCE_CATEGORY_RENAMES.items())
def test_renaming_does_not_change_source_ratings(old, new):
    from backend.services.ranking import source_rating

    item = reference(url="https://studio.test/work/example")
    item.platform, item.source_category = "studio.test", old
    before = source_rating(item)
    item.source_category = new
    assert source_rating(item) == before


def strategy(sources=None, priority=None):
    return SearchStrategy(
        brief_id=str(uuid4()),
        summary="Find relevant creative works",
        preferred_sources=sources or SOURCES,
        source_priority=priority or [],
        strands=[QueryStrand(name="Story", intent="Find concrete works", queries=["human story"])],
    )


@pytest.mark.parametrize("category", SOURCES)
def test_each_category_is_searchable_with_only_its_selected_scope(settings, discovery_api, category):
    settings.search_query_limit = 1
    with TestClient(create_app(live_settings(settings))) as client:
        run = execute(client, sources=[category], count=4)
    assert len(run["results"]) == 1
    item = run["results"][0]
    assert item["source_category"] == category
    assert item["platform"] == publishing_platform(item["source_url"])
    assert item["platform"] not in SOURCES
    assert run["coverage"][0]["references_returned"] == 1
    assert run["usage"]["requests_attempted"] == 2  # Search and curation; no new planning call here.
    call = next(call for call in discovery_api["calls"] if "tools" in call)
    assert json.loads(call["input"])["source_categories"] == [category]
    domains = EXPECTED_CATEGORIES[category][1]
    if domains:
        assert call["tools"][0]["filters"]["allowed_domains"] == domains
    else:
        assert "filters" not in call["tools"][0]


def test_registry_metadata_and_search_only_need_source_configuration(
    settings, discovery_api, tmp_path, monkeypatch
):
    (tmp_path / "sources.json").write_text(
        files("config").joinpath("sources.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setattr(source_registry, "files", lambda package: tmp_path)
    read = source_registry.read_configuration
    read_names = []

    def checked_read(filename, directory):
        assert filename == "sources.json"
        read_names.append(filename)
        return read(filename, directory)

    monkeypatch.setattr(source_registry, "read_configuration", checked_read)
    settings.search_query_limit = 1
    with TestClient(create_app(live_settings(settings))) as client:
        assert len(client.get("/source-categories").json()) == 8
        run = execute(client, sources=["Web & Interactive"], count=4)
    assert run["results"] and set(read_names) == {"sources.json"}


def test_aliases_are_normalized_only_for_new_briefs_and_duplicates_are_collapsed(client):
    response = client.post(
        "/briefs",
        json={
            "concept": "A film",
            "requested_count": 4,
            "preferred_sources": [
                "Video & Film",
                "Video Platforms",
                "Studios & Production Companies",
                "Studios & Agencies",
            ],
        },
    )
    assert response.status_code == 201
    assert response.json()["preferred_sources"] == ["Video Platforms", "Studios & Agencies"]


def test_submitted_strategy_cannot_expand_the_briefs_selected_sources(client):
    brief = client.post(
        "/briefs", json={"concept": "A film", "preferred_sources": ["Video Platforms"]}
    ).json()
    proposed = strategy(["Social Platforms"]).model_dump()
    proposed["brief_id"] = brief["id"]
    response = client.post(
        "/search-runs", json={"brief_id": brief["id"], "strategy": proposed, "idempotency_key": str(uuid4())}
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "source_mismatch"
    assert client.get("/search-runs").json() == []


def test_all_groups_can_be_scheduled_and_priorities_never_expand_scope():
    policy = SourcePolicy()
    assert {job.categories[0] for job in policy.jobs(strategy(), 8)} == set(SOURCES)
    preferred = ["Video Platforms", "Podcasts & Interviews"]
    jobs = policy.jobs(strategy(preferred, ["Podcasts & Interviews"]), 2)
    assert jobs[0].domains == ["gq.com", "gq-magazine.co.uk"]
    assert jobs[1].categories == ["Video Platforms"]
    with pytest.raises(ValidationError):
        strategy(["Video Platforms"], ["Podcasts & Interviews"])
    with pytest.raises(ValidationError):
        strategy(preferred, ["Video Platforms", "Video Platforms"])
    without_podcast = [category for category in SOURCES if category != "Podcasts & Interviews"]
    assert all(
        "Podcasts & Interviews" not in job.categories for job in policy.jobs(strategy(without_podcast), 8)
    )


@pytest.mark.parametrize("count", [1, 2, 4, 8])
def test_every_selected_category_is_covered_before_optional_tasks(count):
    selected = SOURCES[:count]
    priority = list(reversed(selected))
    jobs = SourcePolicy().jobs(strategy(selected, priority), 1)
    assert [job.categories[0] for job in jobs[:count]] == priority
    assert len(jobs) == count


def test_additional_tasks_use_new_strands_after_all_selected_categories():
    planned = strategy(SOURCES[:2])
    planned.strands = [
        QueryStrand(name="Story", intent="Find narratives", queries=["club story film"]),
        QueryStrand(name="Craft", intent="Find execution", queries=["club visual treatment"]),
    ]
    jobs = SourcePolicy().jobs(planned, 4)
    assert len(jobs) == 4
    assert {job.categories[0] for job in jobs[:2]} == set(SOURCES[:2])
    assert len({(tuple(job.categories), tuple(job.queries)) for job in jobs}) == 4


@pytest.mark.parametrize("count", [1, 2, 4, 8])
def test_low_base_budgets_cannot_skip_a_selected_category(settings, discovery_api, count):
    settings.search_query_limit = 1
    settings.search_max_requests = 2
    selected = SOURCES[:count]
    with TestClient(create_app(live_settings(settings))) as client:
        run = execute(client, sources=selected, count=4)
    calls = [call for call in discovery_api["calls"] if "tools" in call]
    assert {json.loads(call["input"])["source_categories"][0] for call in calls} == set(selected)
    assert len(calls) == count
    assert all(entry["queries_attempted"] == 1 for entry in run["coverage"])
    assert run["usage"]["requests_attempted"] == count + 1
    assert settings.search_max_requests == 2  # Per-run expansion never mutates shared settings.
    assert all(call["timeout"] <= settings.search_timeout_seconds for call in calls)


def test_early_timeout_does_not_use_another_selected_categorys_request_slot(settings, discovery_api):
    import httpx
    import openai

    settings.search_retry_count = 2
    discovery_api["failures"][1] = openai.APITimeoutError(
        request=httpx.Request("POST", "https://api.openai.com")
    )
    with TestClient(create_app(live_settings(settings))) as client:
        run = execute(client, count=4)
    calls = [call for call in discovery_api["calls"] if "tools" in call]
    assert len(calls) == len(SOURCES)
    assert len({json.loads(call["input"])["source_categories"][0] for call in calls}) == len(SOURCES)
    assert all(entry["queries_attempted"] == 1 for entry in run["coverage"])
    assert run["usage"]["requests_attempted"] == len(SOURCES) + 1
    assert run["status"] == "partial" and run["results"]


def test_all_sources_are_searched_but_only_highest_scoring_references_are_returned(settings, discovery_api):
    discovery_api["relevance"] = 4
    with TestClient(create_app(live_settings(settings))) as client:
        run = execute(client, count=4)
    assert all(entry["queries_attempted"] >= 1 for entry in run["coverage"])
    assert len(run["results"]) == 4
    scores = [item["ranking"]["score"] for item in run["results"]]
    assert scores == sorted(scores, reverse=True)
    assert [item["source_category"] for item in run["results"][:3]] == [
        "Studios & Agencies",
        "Award Archives",
        "Video Platforms",
    ]
    assert "Social Platforms" not in {item["source_category"] for item in run["results"]}


@pytest.mark.parametrize(
    "prompt,first",
    [
        ("An interactive website", "Web & Interactive"),
        ("A podcast interview", "Podcasts & Interviews"),
    ],
)
def test_legacy_strategies_get_relevant_local_fallback_without_an_extra_model_call(prompt, first):
    jobs = SourcePolicy().jobs(strategy(), 4, StoredBrief(concept=prompt))
    assert jobs[0].categories == [first]
    assert len(jobs) == len(SOURCES)


def test_existing_planning_call_provides_priority_within_the_same_search_budget(settings, discovery_api):
    discovery_api["source_priority"] = ["Web & Interactive"]
    with TestClient(create_app(live_settings(settings))) as client:
        run = execute(client, count=4)
    search_calls = [call for call in discovery_api["calls"] if "tools" in call]
    assert search_calls[0]["tools"][0]["filters"]["allowed_domains"] == [
        "awwwards.com",
        "siteinspire.com",
        "webflow.com",
    ]
    assert run["strategy"]["source_priority"] == ["Web & Interactive"]
    assert run["usage"]["requests_attempted"] == len(SOURCES) + 1


def test_out_of_scope_model_priority_is_rejected_before_search(settings, discovery_api):
    discovery_api["source_priority"] = ["Podcasts & Interviews"]
    with TestClient(create_app(live_settings(settings))) as client:
        brief = client.post(
            "/briefs", json={"concept": "A film", "preferred_sources": ["Video Platforms"]}
        ).json()
        response = client.post(f"/briefs/{brief['id']}/strategy")
    assert response.status_code == 502
    assert discovery_api["web_attempts"] == 0


def test_domain_attribution_rejects_relabelled_unselected_sources():
    policy = SourcePolicy()
    studios = ["Studios & Agencies"]
    assert policy.category("https://youtube.com/watch?v=abc", studios[0], studios) is None
    assert policy.category("https://thedrum.com/work/example", studios[0], studios) is None
    assert policy.category("https://studio.test/work/example", studios[0], studios) == studios[0]
    assert policy.category("https://vimeo.com/1234", "Video Platforms", SOURCES, ["Award Archives"]) is None


def test_new_publisher_does_not_crash_ranking_or_upgrade_repost_authority():
    publisher = reference(url="https://thedrum.com/work/shared-futures")
    publisher.platform, publisher.source_category = "thedrum.com", "Creative Press"
    assert score_reference(publisher, StoredBrief(concept="A warm film"), set(publisher.evidence_ids))
    assert publisher.ranking.components["source"] == 3.5
    publisher.platform, publisher.source_category = "Pinterest", "Social Platforms"
    assert score_reference(publisher, StoredBrief(concept="A warm film"), set(publisher.evidence_ids))
    assert publisher.ranking.components["source"] == 2.5


def test_submission_without_optional_priority_can_be_retried(client):
    brief = client.post(
        "/briefs", json={"concept": "A film", "requested_count": 4, "preferred_sources": ["Video Platforms"]}
    ).json()
    old_strategy = strategy(["Video Platforms"]).model_dump()
    old_strategy["brief_id"] = brief["id"]
    old_strategy.pop("source_priority")
    old_strategy.pop("research_title")
    old_strategy.pop("constraints")
    request = {"brief_id": brief["id"], "strategy": old_strategy, "idempotency_key": str(uuid4())}
    first = client.post("/search-runs", json=request)
    first.raise_for_status()
    fingerprint = hashlib.sha256(
        json.dumps(
            {key: value for key, value in request.items() if key != "idempotency_key"}, sort_keys=True
        ).encode()
    ).hexdigest()
    with client.app.state.workflow.repository.connection() as connection:
        stored = connection.execute(
            "SELECT fingerprint FROM submissions WHERE key=?", (request["idempotency_key"],)
        ).fetchone()[0]
    assert stored == fingerprint
    assert client.post("/search-runs", json=request).json()["id"] == first.json()["id"]
