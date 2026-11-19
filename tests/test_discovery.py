import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import openai
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from streamlit.testing.v1 import AppTest

from backend.main import create_app
from backend.schemas.models import (
    MAX_REFERENCES,
    Annotation,
    CreativeAssessment,
    DiscoveredReference,
    DiscoveryDraft,
    PageCheck,
    QueryStrand,
    StrategyDraft,
)
from backend.services.source_policy import canonical_url
from frontend.api_client import APIClient, APIError


def api_response(data, parsed=None):
    return SimpleNamespace(
        status=data.get("status", "completed"),
        output_parsed=parsed,
        output=[
            SimpleNamespace(type=item["type"], status=item.get("status")) for item in data.get("output", [])
        ],
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        model_dump=lambda **kwargs: data,
    )


@pytest.fixture
def discovery_api(monkeypatch):
    state = {
        "calls": [],
        "web_attempts": 0,
        "failures": {},
        "inject_unknown": False,
        "no_tool": False,
        "no_citations": False,
        "page_status": "accessible",
        "generic_page": False,
        "relevance": None,
        "unidentified_page": False,
    }

    def checked_pages(self, urls, **kwargs):
        self.checked = len(urls)
        result = {}
        for url in urls:
            title = "Shared Futures " + url.rsplit("-", 1)[-1]
            result[url] = PageCheck(
                original_url=url,
                final_url=url,
                title=title,
                text=f"{title} by Example Studio. An environmental campaign with warm stories and humour. "
                * 3,
                status=state["page_status"],
                work_page=not state["generic_page"],
                reason="Test-only page check.",
            )
            if state["unidentified_page"]:
                result[url].title = ""
                result[url].text = "A readable but unrelated page with no identifiable work title. " * 5
        return result

    monkeypatch.setattr("backend.providers.discovery.PageVerifier.check_many", checked_pages)

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] in {0, 1}
            self.responses = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def create(self, **kwargs):
            state["calls"].append(kwargs)
            state["web_attempts"] += 1
            index = state["web_attempts"]
            if index in state["failures"]:
                raise state["failures"][index]
            payload = json.loads(kwargs["input"])
            category = payload["source_categories"][0]
            domains = {
                "Video Platforms": "vimeo.com",
                "Award Archives": "dandad.org",
                "Creative Press": "thedrum.com",
                "Brand Film Showcases": "nowadays.tv",
                "Web & Interactive": "awwwards.com",
                "Podcasts & Interviews": "gq.com",
                "Social Platforms": "pinterest.com",
            }
            url = f"https://{domains.get(category, 'studio.test')}/work/shared-futures-{index}"
            title = f"Shared Futures {index}"
            text = (
                f"{title} by Example Studio. A character-led environmental campaign. "
                "A warm story with a comic reveal."
            )
            calls = (
                []
                if state["no_tool"]
                else [
                    {
                        "type": "web_search_call",
                        "status": "completed",
                        "action": {"type": "search", "sources": [{"url": url, "title": title}]},
                    },
                    {"type": "web_search_call", "status": "searching", "action": {"type": "search"}},
                ]
            )
            citations = (
                []
                if state["no_citations"]
                else [
                    {
                        "type": "url_citation",
                        "url": url,
                        "title": title,
                        "start_index": len(text) - 1,
                        "end_index": len(text),
                    }
                ]
            )
            return api_response(
                {
                    "output": calls
                    + [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": text,
                                    "annotations": citations,
                                }
                            ],
                        }
                    ]
                }
            )

        def parse(self, **kwargs):
            if kwargs["text_format"] is StrategyDraft:
                draft = StrategyDraft(
                    research_title="Human Environmental Campaign Films",
                    source_priority=state.get("source_priority", []),
                    summary="Research warm environmental films",
                    assumptions=[],
                    strands=[
                        QueryStrand(
                            name=name, intent=f"Explore {name}", queries=[f"environmental film {name}"]
                        )
                        for name in ["Tone", "Craft", "Format"]
                    ],
                )
                return api_response({}, draft)
            state["calls"].append(kwargs)
            context = json.loads(kwargs["input"])
            candidates = []
            for source in context["sources"]:
                candidates.append(
                    DiscoveredReference(
                        title=source["title"],
                        creator="Example Studio",
                        primary_evidence_id=source["id"],
                        supporting_evidence_ids=[],
                        platform=source["source_categories"][0],
                        tags=["story"],
                        strand=source["strand"],
                        annotation=Annotation(
                            what_it_is=source["search_summary"],
                            what_to_borrow="A character-led narrative",
                            why_it_fits="Connects an environmental topic with a human perspective.",
                            production_note="Casting and access need producer review.",
                            confidence="high",
                        ),
                        assessment=CreativeAssessment(
                            relevance=state["relevance"],
                            specificity=4,
                            craft=3,
                            feasibility=3,
                            role="close_match",
                            reasons=["A specific connection to the supplied creative route."],
                            evidence_ids=[source["id"]],
                        )
                        if state["relevance"] is not None
                        else None,
                    )
                )
            if state["inject_unknown"]:
                fake = candidates[0].model_copy(
                    update={"title": "Invented work", "primary_evidence_id": "unknown"}
                )
                candidates.insert(0, fake)
            return api_response({}, DiscoveryDraft(references=candidates[:MAX_REFERENCES]))

    monkeypatch.setattr(openai, "OpenAI", Client)
    return state


def execute(client, count=3, sources=None):
    response = client.post(
        "/briefs",
        json={
            "concept": "A warm environmental film with unexpected humour",
            "requested_count": count,
            **({"preferred_sources": sources} if sources else {}),
        },
    )
    assert response.status_code == 201
    brief = response.json()
    response = client.post(f"/briefs/{brief['id']}/strategy")
    assert response.status_code == 200, response.text
    response = client.post(
        "/search-runs",
        json={
            "brief_id": brief["id"],
            "strategy": response.json(),
            "idempotency_key": str(uuid4()),
        },
    )
    assert response.status_code == 202, response.text
    return client.get(f"/search-runs/{response.json()['id']}").json()


def live_settings(settings):
    settings.app_mode = "live"
    settings.openai_api_key = SecretStr("test-key-do-not-return")
    return settings


def test_additional_search_receives_exclusions_and_removes_them_before_final_top_n(settings, discovery_api):
    from backend.providers.discovery import ExternalDiscovery
    from backend.schemas.models import StoredBrief

    with TestClient(create_app(live_settings(settings))) as client:
        original = execute(client, count=4)
        previous = client.app.state.workflow.run(original["id"]).results
        brief = StoredBrief(concept="More creative environmental films", requested_count=4)
        strategy = client.app.state.workflow.provider.strategy(brief)
        start = discovery_api["web_attempts"]
        baseline = ExternalDiscovery(settings).search(brief, strategy)
        excluded = baseline.results[:2]
        discovery_api["web_attempts"] = start
        outcome = ExternalDiscovery(settings).search(brief, strategy, excluded_works=excluded + previous)
        assert len(outcome.results) == 4
        excluded_titles = {item.title for item in excluded + previous}
        assert not any(item.title in excluded_titles for item in outcome.results)
        calls = [json.loads(call["input"]) for call in discovery_api["calls"]]
        additional = [call for call in calls if "excluded_works" in call]
        assert additional and any("candidate_limit" in call for call in additional)
        assert all(len(call["excluded_works"]) == len(excluded + previous) for call in additional)


def test_live_search_records_sources_usage_and_exports(settings, discovery_api):
    with TestClient(create_app(live_settings(settings))) as client:
        assert not client.get("/health").json()["live_search_verified"]
        run = execute(client)
        assert run["status"] == "completed"
        assert len(run["results"]) == 3
        assert run["usage"]["web_search_calls"] == 8
        assert run["usage"]["requests_attempted"] == 9
        assert run["usage"]["input_tokens"] == 900
        assert len(run["coverage"]) == 8
        assert all(item["queries_attempted"] >= 1 for item in run["coverage"])
        assert all(
            not item["is_mock"] and item["verification_status"] == "supported" for item in run["results"]
        )
        assert all(item["annotation"]["confidence"] == "medium" for item in run["results"])
        assert all(item["ranking"] for item in run["results"])
        assert client.get("/health").json()["live_search_verified"]
        assert "test-key-do-not-return" not in json.dumps(run)
        saved = client.put(
            f"/search-runs/{run['id']}/selection",
            json={
                "ordered_ids": [run["results"][0]["id"]],
                "expected_version": 0,
            },
        )
        assert saved.status_code == 200
        assert client.post(f"/search-runs/{run['id']}/exports/docx").status_code == 201
        for call in discovery_api["calls"]:
            assert call["store"] is False
            if "tools" in call:
                assert call["tool_choice"] == "required" and call["max_tool_calls"] == 1


def test_twenty_reference_request_expands_curation_capacity_without_filling_missing_evidence(
    settings, discovery_api
):
    with TestClient(create_app(live_settings(settings))) as client:
        run = execute(client, count=20)
    # Eight test-only source records remain eight results; missing evidence is never fabricated.
    assert run["status"] == "partial" and len(run["results"]) == 8
    curation = next(call for call in discovery_api["calls"] if "text_format" in call)
    assert json.loads(curation["input"])["candidate_limit"] == 20
    assert curation["max_output_tokens"] == 15400


def test_only_grounded_ids_can_become_reference_urls(settings, discovery_api):
    discovery_api["inject_unknown"] = True
    with TestClient(create_app(live_settings(settings))) as client:
        run = execute(client)
    assert len(run["results"]) == 3
    assert all(item["title"] != "Invented work" for item in run["results"])
    assert any("Excluded" in warning for warning in run["warnings"])


def test_restricted_pages_keep_search_only_evidence_with_low_confidence(settings, discovery_api):
    discovery_api["page_status"] = "restricted"
    with TestClient(create_app(live_settings(settings))) as client:
        run = execute(client)
    assert run["status"] == "partial" and run["results"]
    assert all(item["verification_status"] == "unverified" for item in run["results"])
    assert all(item["annotation"]["confidence"] == "low" for item in run["results"])
    assert not any(item["evidence_type"] == "page_text" for item in run["evidence"])
    assert all(check["status"] == "restricted" for check in run["page_checks"])


@pytest.mark.parametrize("mode", ["dead", "generic"])
def test_dead_links_and_generic_pages_cannot_become_recommendations(settings, discovery_api, mode):
    discovery_api["page_status"] = "unreachable" if mode == "dead" else "accessible"
    discovery_api["generic_page"] = mode == "generic"
    with TestClient(create_app(live_settings(settings))) as client:
        run = execute(client)
    assert run["status"] == "failed" and not run["results"]
    assert run["page_checks"]
    assert run["usage"]["requests_attempted"] == 8  # No curation call when every source is unusable.


def test_consulted_only_source_needs_readable_work_text_before_recommendation(settings, discovery_api):
    discovery_api["no_citations"] = True
    with TestClient(create_app(live_settings(settings))) as client:
        run = execute(client)
    assert run["results"] and all(item["verification_status"] == "supported" for item in run["results"])
    assert any(item["evidence_type"] == "page_text" for item in run["evidence"])


def test_model_relevance_gate_excludes_off_brief_work_even_when_page_exists(settings, discovery_api):
    discovery_api["relevance"] = 1
    with TestClient(create_app(live_settings(settings))) as client:
        run = execute(client)
    assert run["status"] == "failed" and not run["results"]
    assert run["error_code"] == "no_eligible_references"


def test_search_title_is_not_reused_as_independent_page_identity(settings, discovery_api):
    discovery_api["unidentified_page"] = True
    with TestClient(create_app(live_settings(settings))) as client:
        run = execute(client)
    assert run["status"] == "failed" and not run["results"]


def test_domain_preferences_and_budget_are_enforced(settings, discovery_api):
    settings = live_settings(settings)
    settings.search_max_requests = 3
    with TestClient(create_app(settings)) as client:
        run = execute(client, sources=["Video Platforms"])
    assert run["status"] == "partial"
    assert 0 < len(run["results"]) < 3
    assert run["usage"]["requests_attempted"] <= 3
    assert run["usage"]["web_search_calls"] == 2
    for call in discovery_api["calls"]:
        if "tools" in call:
            assert call["tools"][0]["filters"]["allowed_domains"] == ["youtube.com", "youtu.be", "vimeo.com"]


def test_failed_group_keeps_other_source_results(settings, discovery_api):
    settings = live_settings(settings)
    settings.search_retry_count = 0
    discovery_api["failures"][2] = openai.APITimeoutError(
        request=httpx.Request("POST", "https://api.openai.com")
    )
    with TestClient(create_app(settings)) as client:
        run = execute(client)
    assert run["status"] == "partial"
    assert run["results"] and run["usage"]["web_search_calls"] == 7
    assert any("timeout" in warning.lower() for warning in run["warnings"])


def test_transient_failure_retries_within_global_budget(settings, discovery_api):
    settings.search_max_requests = 10  # Eight searches, one curation, one spare retry.
    discovery_api["failures"][1] = openai.APITimeoutError(
        request=httpx.Request("POST", "https://api.openai.com")
    )
    with TestClient(create_app(live_settings(settings))) as client:
        run = execute(client)
    assert run["results"]
    assert run["usage"]["requests_attempted"] == 10
    assert run["usage"]["requests_succeeded"] == 9


@pytest.mark.parametrize("mode", ["no_tool", "no_citations"])
def test_search_without_tool_or_cited_work_cannot_fake_success(settings, discovery_api, mode):
    discovery_api[mode] = True
    discovery_api["page_status"] = "unverified"
    with TestClient(create_app(live_settings(settings))) as client:
        run = execute(client)
    assert run["status"] == "failed" and not run["results"]
    assert run["error_code"] in {"search_not_executed", "no_search_evidence"}


def test_authentication_failure_stops_and_never_exposes_upstream_body(settings, discovery_api):
    response = httpx.Response(401, request=httpx.Request("POST", "https://api.openai.com"))
    discovery_api["failures"][1] = openai.AuthenticationError("sensitive detail", response=response, body={})
    with TestClient(create_app(live_settings(settings))) as client:
        run = execute(client)
    assert run["status"] == "failed" and run["error_code"] == "openai_authentication"
    assert run["usage"]["requests_attempted"] == 1
    assert "sensitive detail" not in json.dumps(run)
    assert all(
        "openai_authentication" in entry["note"]
        for entry in run["coverage"]
        if entry["queries_attempted"] == 0
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/work",
        "http://10.0.0.1",
        "http://localhost/x",
        "javascript:alert(1)",
        "https://example.invalid/work",
        "https://youtube.com@127.0.0.1/",
    ],
)
def test_non_public_or_unsafe_reference_links_are_rejected(url):
    assert canonical_url(url) is None


def test_live_frontend_can_search_curate_and_export(settings, discovery_api, monkeypatch):
    with TestClient(create_app(live_settings(settings))) as client:

        def request(self, method, path, payload=None, *, binary=False):
            response = client.request(method, path, json=payload)
            if response.is_error:
                raise APIError(response.json()["error"]["message"])
            return response.content if binary else response.json()

        monkeypatch.setattr(APIClient, "request", request)
        app = AppTest.from_file(
            str(Path(__file__).resolve().parents[1] / "frontend/app.py"), default_timeout=15
        ).run()
        assert not app.exception
        next(item for item in app.text_area if item.label == "Describe your creative idea").input(
            "A warm sustainability film"
        )
        app.number_input[0].set_value(3)
        next(item for item in app.button if item.label == "Find References").click().run()
        assert not app.exception
        choices = [item for item in app.button if (item.key or "").startswith("pick_action_")]
        assert len(choices) == 3
        choices[0].click().run()
        next(item for item in app.button if item.label == "Export Reference Pack").click().run()
        assert not app.exception
        run_id = app.session_state["run_id"]
        assert app.session_state["pack_downloads"][run_id]["content"].startswith(b"PK")


def test_url_cleanup_preserves_access_and_video_identifiers():
    assert (
        canonical_url("https://vimeo.com/123?h=private-hash&utm_source=x")
        == "https://vimeo.com/123?h=private-hash"
    )
    assert (
        canonical_url("https://www.youtube.com/watch?v=abc&utm_medium=x")
        == "https://www.youtube.com/watch?v=abc"
    )
