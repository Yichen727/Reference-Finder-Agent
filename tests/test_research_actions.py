"""Additional batches and linked versions use isolated, offline research."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.core.errors import AppError
from backend.main import create_app
from backend.schemas.models import ResearchActionInput, SourceCoverage, new_id
from config import SOURCE_CATEGORY_RENAMES
from tests.conftest import completed_run
from tests.fixtures import FixtureProvider
from tests.pptx_helpers import slide_text, slides_from_bytes


def action_input(action="add", count=4, **brief):
    return {
        "action": action,
        "brief": {"concept": "An updated creative direction", "requested_count": count,
                  "preferred_sources": ["Video Platforms"], **brief},
        "idempotency_key": str(uuid4()),
    }


def submit(client, parent, payload=None):
    response = client.post(f"/search-runs/{parent['id']}/actions", json=payload or action_input())
    assert response.status_code == 202, response.text
    return client.get(f"/search-runs/{response.json()['id']}").json()


def save_pack(client, run):
    chosen = [run["results"][2], run["results"][0]]
    value = {
        "ordered_ids": [item["id"] for item in chosen], "expected_version": 0,
        "annotation_overrides": {chosen[0]["id"]: {**chosen[0]["annotation"],
                                                    "what_to_borrow": "Keep this manual edit."}},
        "rejected_ids": [run["results"][1]["id"]], "rejection_notes": "Keep this exclusion.",
    }
    saved = client.put(f"/search-runs/{run['id']}/selection", json=value)
    assert saved.status_code == 200, saved.text
    return saved.json()


def test_add_persists_parameters_preserves_pack_and_appends_once(client, settings):
    original, _ = completed_run(client, count=4)
    selection = save_pack(client, original)
    original = client.get(f"/search-runs/{original['id']}").json()
    payload = action_input()
    batch = submit(client, original, payload)
    assert batch["status"] == "completed" and batch["operation"] == "add"
    assert batch["parent_run_id"] == original["id"]
    updated = client.get(f"/search-runs/{original['id']}").json()
    assert len(updated["results"]) == 8
    assert updated["results"][:4] == original["results"]
    assert updated["selection"] == selection
    assert updated["strategy"] == original["strategy"] and updated["brief_id"] == original["brief_id"]
    assert updated["appended_batch_ids"] == [batch["id"]]
    assert batch["added_reference_ids"] == [item["id"] for item in updated["results"][4:]]
    brief = client.get(f"/briefs/{batch['brief_id']}").json()
    assert all(brief[key] == value for key, value in payload["brief"].items())
    assert [run["id"] for run in client.get("/search-runs").json()] == [original["id"]]
    assert client.get(f"/search-runs/{original['id']}/actions").json()[0]["id"] == batch["id"]
    assert submit(client, original, payload)["id"] == batch["id"]
    client.app.state.workflow.execute(batch["id"])
    assert client.get(f"/search-runs/{original['id']}").json() == updated
    with TestClient(create_app(settings)) as restored:
        assert restored.get(f"/search-runs/{original['id']}").json() == updated


def test_add_deduplicates_selected_rejected_and_unselected_works(client):
    original, _ = completed_run(client, count=4)
    save_pack(client, original)
    workflow = client.app.state.workflow
    previous = workflow.run(original["id"]).results

    def search(brief, strategy, progress=None, *, excluded_works=None):
        assert [item.id for item in excluded_works] == [item.id for item in previous]
        outcome = FixtureProvider().search(brief, strategy, excluded_works=excluded_works)
        outcome.results[:2] = [item.model_copy(update={"id": new_id()}) for item in previous[:2]]
        outcome.evidence += workflow.run(original["id"]).evidence
        return outcome

    workflow.provider.search = search
    batch = submit(client, original)
    assert batch["status"] == "partial" and len(batch["results"]) == 2
    assert len(client.get(f"/search-runs/{original['id']}").json()["results"]) == 6


@pytest.mark.parametrize("failure", ["planning", "search", "duplicates", "empty"])
def test_failed_batch_leaves_original_unchanged(client, failure):
    original, _ = completed_run(client, count=4)
    save_pack(client, original)
    original = client.get(f"/search-runs/{original['id']}").json()
    workflow = client.app.state.workflow

    def fail(*args, **kwargs):
        raise AppError("test_failure", "Test-only failure.", 502)

    def duplicates(brief, strategy, progress=None, *, excluded_works=None):
        outcome = FixtureProvider().search(brief, strategy, excluded_works=excluded_works)
        outcome.results = [] if failure == "empty" else workflow.run(original["id"]).results
        outcome.evidence = workflow.run(original["id"]).evidence
        return outcome

    if failure == "planning":
        workflow.provider.strategy = fail
    else:
        workflow.provider.search = fail if failure == "search" else duplicates
    batch = submit(client, original)
    assert batch["status"] == "failed"
    assert client.get(f"/search-runs/{original['id']}").json() == original


def test_pack_changes_during_search_survive_the_atomic_append(client):
    original, _ = completed_run(client, count=4)
    workflow = client.app.state.workflow
    saved = []

    def search(brief, strategy, progress=None, *, excluded_works=None):
        saved.append(save_pack(client, original))
        return FixtureProvider().search(brief, strategy, progress, excluded_works=excluded_works)

    workflow.provider.search = search
    assert submit(client, original)["status"] == "completed"
    updated = client.get(f"/search-runs/{original['id']}").json()
    assert updated["selection"] == saved[0] and len(updated["results"]) == 8
    assert client.put(f"/search-runs/{original['id']}/selection", json={
        "ordered_ids": [], "expected_version": 0,
    }).status_code == 409


def test_regenerate_creates_linked_version_preserving_original_pack_and_exports(client):
    original, _ = completed_run(client, count=4)
    save_pack(client, original)
    original = client.get(f"/search-runs/{original['id']}").json()
    record = client.post(f"/search-runs/{original['id']}/exports/pptx").json()
    content = client.get(f"/exports/{record['id']}/download").content
    regenerated = submit(client, original, action_input("regenerate"))
    assert regenerated["id"] != original["id"] and regenerated["parent_run_id"] == original["id"]
    assert regenerated["operation"] == "regenerate" and regenerated["selection"] is None
    assert regenerated["status"] == "completed" and len(regenerated["results"]) == 4
    assert client.get(f"/search-runs/{original['id']}").json() == original
    assert client.get(f"/exports/{record['id']}/download").content == content
    assert {item["id"] for item in client.get("/search-runs").json()} == {
        original["id"], regenerated["id"],
    }


def test_additions_can_accumulate_above_twenty_but_pack_limit_is_unchanged(client):
    original, _ = completed_run(client, count=20)
    assert submit(client, original)["status"] == "completed"
    updated = client.get(f"/search-runs/{original['id']}").json()
    assert len(updated["results"]) == 24
    assert client.put(f"/search-runs/{original['id']}/selection", json={
        "ordered_ids": [item["id"] for item in updated["results"]], "expected_version": 0,
    }).status_code == 422
    assert client.put(f"/search-runs/{original['id']}/selection", json={
        "ordered_ids": [], "rejected_ids": [item["id"] for item in updated["results"]],
        "expected_version": 0,
    }).status_code == 200


def test_later_batches_exclude_every_previously_appended_result(client):
    original, _ = completed_run(client, count=4)
    first = submit(client, original)
    second = submit(client, original)
    updated = client.get(f"/search-runs/{original['id']}").json()
    assert len(updated["results"]) == 12
    assert updated["appended_batch_ids"] == [first["id"], second["id"]]
    assert len({item["title"] for item in updated["results"]}) == 12


@pytest.mark.parametrize("rename", [False, True])
def test_queued_version_does_not_cache_a_placeholder_instead_of_the_ai_title(client, rename):
    original, _ = completed_run(client, count=4)
    workflow = client.app.state.workflow
    task, _ = workflow.submit_action(original["id"], ResearchActionInput(**action_input("regenerate")))
    listed = next(item for item in client.get("/search-runs").json() if item["id"] == task.id)
    assert listed["title"] == "Research in progress"
    if rename:
        response = client.patch(f"/search-runs/{task.id}/title", json={"title": "My Version Title"})
        assert response.status_code == 200
    workflow.execute(task.id)
    listed = next(item for item in client.get("/search-runs").json() if item["id"] == task.id)
    assert listed["title"] == ("My Version Title" if rename else "Human Side of AI")


def test_failed_version_is_not_labelled_as_still_in_progress(client):
    original, _ = completed_run(client, count=4)

    def fail(*args, **kwargs):
        raise AppError("test_failure", "Test-only planning failure.", 502)

    client.app.state.workflow.provider.strategy = fail
    version = submit(client, original, action_input("regenerate"))
    listed = next(item for item in client.get("/search-runs").json() if item["id"] == version["id"])
    assert listed["status"] == "failed" and listed["title"] == "Research did not complete"


def test_legacy_research_without_action_metadata_still_accepts_an_addition(client):
    original, _ = completed_run(client, count=4)
    raw = {key: value for key, value in original.items() if key not in {
        "operation", "parent_run_id", "planning_pending", "appended_batch_ids", "added_reference_ids",
    }}
    with client.app.state.workflow.repository.connection() as conn:
        conn.execute(
            "UPDATE records SET payload=? WHERE kind='run' AND id=?", (json.dumps(raw), original["id"])
        )
    assert submit(client, original)["status"] == "completed"
    assert len(client.get(f"/search-runs/{original['id']}").json()["results"]) == 8


def test_legacy_coverage_labels_merge_into_eight_categories_without_changing_old_cards(client):
    original, _ = completed_run(client, count=4)
    workflow = client.app.state.workflow
    stored = workflow.run(original["id"])
    stored.coverage = [SourceCoverage(category=name, queries_attempted=1)
                       for name in [*SOURCE_CATEGORY_RENAMES, "Podcasts & Interviews"]]
    stored.results[0].source_category = "Video & Film"
    workflow.repository.put("run", stored)
    previous_cards = stored.model_dump(mode="json")["results"]

    def search(brief, strategy, progress=None, *, excluded_works=None):
        outcome = FixtureProvider().search(brief, strategy, excluded_works=excluded_works)
        outcome.coverage = [SourceCoverage(category=name, queries_attempted=1)
                            for name in [*SOURCE_CATEGORY_RENAMES.values(), "Podcasts & Interviews"]]
        return outcome

    workflow.provider.search = search
    batch = submit(client, original)
    assert batch["status"] == "completed"
    updated = workflow.run(original["id"])
    assert len(updated.coverage) == 8 and all(item.queries_attempted == 2 for item in updated.coverage)
    assert updated.model_dump(mode="json")["results"][:4] == previous_cards


def test_new_references_export_in_saved_pack_order(client):
    original, _ = completed_run(client, count=4)
    submit(client, original)
    updated = client.get(f"/search-runs/{original['id']}").json()
    chosen = [updated["results"][-1], updated["results"][0]]
    assert client.put(f"/search-runs/{original['id']}/selection", json={
        "ordered_ids": [item["id"] for item in chosen], "expected_version": 0,
    }).status_code == 200
    record = client.post(f"/search-runs/{original['id']}/exports/pptx").json()
    slides = slides_from_bytes(client.get(f"/exports/{record['id']}/download").content)
    assert len(slides) == 5
    assert all(item["title"].upper() in slide_text(slide) for slide, item in zip(slides[2:4], chosen))


def test_duplicate_submission_conflict_and_busy_checks_are_atomic(client):
    original, _ = completed_run(client, count=4)
    workflow = client.app.state.workflow
    payload = action_input()
    request = ResearchActionInput(**payload)
    pending, created = workflow.submit_action(original["id"], request)
    assert created
    assert workflow.submit_action(original["id"], request)[1] is False
    assert client.post(f"/search-runs/{original['id']}/actions", json=action_input()).status_code == 409
    changed = copy.deepcopy(payload)
    changed["brief"]["concept"] = "Different parameters"
    assert client.post(f"/search-runs/{original['id']}/actions", json=changed).status_code == 409
    assert client.delete(f"/search-runs/{original['id']}").status_code == 409
    workflow.execute(pending.id)
    assert len(workflow.run(original["id"]).results) == 8


def test_concurrent_submissions_create_only_one_batch(client):
    original, _ = completed_run(client, count=4)
    workflow = client.app.state.workflow

    def submit_one(index):
        try:
            return workflow.submit_action(original["id"], ResearchActionInput(**action_input()))[1]
        except AppError as error:
            assert error.code == "research_busy"
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(submit_one, range(4))) == 1


def test_restart_preserves_parent_and_interrupts_pending_batch(client, settings):
    original, _ = completed_run(client, count=4)
    pending, _ = client.app.state.workflow.submit_action(
        original["id"], ResearchActionInput(**action_input())
    )
    with TestClient(create_app(settings)) as restored:
        assert restored.get(f"/search-runs/{original['id']}").json() == original
        assert restored.get(f"/search-runs/{pending.id}").json()["status"] == "interrupted"


def test_deleted_parent_hides_batches_but_linked_versions_remain(client):
    original, _ = completed_run(client, count=4)
    batch = submit(client, original)
    version = submit(client, original, action_input("regenerate"))
    assert client.delete(f"/search-runs/{original['id']}").status_code == 200
    assert client.get(f"/search-runs/{batch['id']}").status_code == 404
    assert client.get(f"/search-runs/{version['id']}").status_code == 200


@pytest.mark.parametrize("change", [{"requested_count": 21}, {"preferred_sources": []}, {"concept": " "}])
def test_invalid_action_parameters_are_rejected_without_changing_research(client, change):
    original, _ = completed_run(client, count=4)
    response = client.post(f"/search-runs/{original['id']}/actions", json=action_input(**change))
    assert response.status_code == 422
    assert client.get(f"/search-runs/{original['id']}").json() == original
