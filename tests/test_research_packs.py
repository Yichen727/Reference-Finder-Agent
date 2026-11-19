"""Every new research owns one empty pack; additions and navigation keep existing packs."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.schemas.models import ResearchSubmissionInput
from tests.conftest import completed_run
from tests.test_library import add, current, locator
from tests.test_research_actions import action_input, submit


def queued_request(key="new-research"):
    return {
        "brief": {"concept": "A new community film", "requested_count": 4},
        "idempotency_key": key,
    }


def test_new_research_creates_and_activates_exactly_one_empty_named_pack(client, settings):
    initial = client.get("/library").json()
    run, submission = completed_run(client, count=4)
    state = client.get("/library").json()
    pack = state["current_pack"]
    assert len(state["packs"]) == len(initial["packs"]) + 1
    assert pack["name"] == run["strategy"]["research_title"]
    assert not pack["ordered_ids"] and pack["id"] != initial["current_pack"]["id"]
    pack = add(client, pack, locator(run))
    retried = client.post("/search-runs", json=submission)
    assert retried.status_code == 202 and retried.json()["id"] == run["id"]
    assert current(client) == pack
    # A legacy selection saved later must not cause a duplicate import on restart.
    client.put(
        f"/search-runs/{run['id']}/selection",
        json={"ordered_ids": [run["results"][1]["id"]], "expected_version": 0},
    ).raise_for_status()
    with TestClient(create_app(settings)) as restarted:
        assert current(restarted) == pack
        assert len(restarted.get("/library").json()["packs"]) == len(state["packs"])


@pytest.mark.parametrize("manual_name", [False, True])
def test_queued_research_pack_is_created_before_search_and_uses_existing_title(
    client, monkeypatch, manual_name,
):
    workflow = client.app.state.workflow
    execute = workflow.execute
    monkeypatch.setattr(workflow, "execute", lambda _: None)
    original = current(client)
    payload = queued_request()
    response = client.post("/search-runs", json=payload)
    assert response.status_code == 202
    run = response.json()
    pack = current(client)
    assert run["planning_pending"] and pack["name"] == "New Research Pack"
    assert pack["id"] != original["id"] and not pack["ordered_ids"]
    if manual_name:
        client.patch(
            f"/packs/{pack['id']}", json={"name": "My Chosen Name", "expected_version": pack["version"]},
        ).raise_for_status()
    client.put(f"/packs/{original['id']}/activate").raise_for_status()
    # A retry or background completion must not override an intentional pack switch.
    client.post("/search-runs", json=payload).raise_for_status()
    assert current(client)["id"] == original["id"]
    execute(run["id"])
    state = client.get("/library").json()
    assert state["current_pack"]["id"] == original["id"]
    saved = client.get(f"/packs/{pack['id']}").json()
    finished = client.get(f"/search-runs/{run['id']}").json()
    expected = "My Chosen Name" if manual_name else finished["strategy"]["research_title"]
    assert saved["name"] == expected
    assert len(state["packs"]) == 2 and not saved["ordered_ids"]


def test_add_keeps_pack_but_regenerate_creates_one_new_pack(client):
    run, _ = completed_run(client, count=4)
    original = add(client, current(client), locator(run))
    count = len(client.get("/library").json()["packs"])
    submit(client, run, action_input("add"))
    assert current(client) == original
    assert len(client.get("/library").json()["packs"]) == count
    payload = action_input("regenerate")
    version = submit(client, run, payload)
    state = client.get("/library").json()
    assert len(state["packs"]) == count + 1
    assert state["current_pack"]["id"] != original["id"]
    assert not state["current_pack"]["ordered_ids"]
    assert state["current_pack"]["name"] == version["strategy"]["research_title"]
    assert client.get(f"/packs/{original['id']}").json() == original
    submit(client, run, payload)
    assert len(client.get("/library").json()["packs"]) == count + 1


def test_deleting_a_research_or_its_pack_never_deletes_other_records_or_recreates_a_pack(client, settings):
    run, submission = completed_run(client, count=4)
    pack = add(client, current(client), locator(run))
    exported = client.post(
        f"/packs/{pack['id']}/exports/pptx", json={"expected_version": pack["version"]},
    )
    assert exported.status_code == 201
    client.delete(f"/search-runs/{run['id']}").raise_for_status()
    assert client.get(f"/packs/{pack['id']}").json() == pack
    assert client.get(f"/exports/{exported.json()['id']}/download").status_code == 200
    assert client.post("/search-runs", json=submission).status_code == 404
    client.request(
        "DELETE", f"/packs/{pack['id']}", json={"expected_version": pack["version"]},
    ).raise_for_status()
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/packs/{pack['id']}").status_code == 404
        assert len(restarted.get("/library").json()["packs"]) == 1


def test_concurrent_retries_create_a_single_research_and_pack(client):
    workflow = client.app.state.workflow
    value = ResearchSubmissionInput.model_validate(queued_request())
    with ThreadPoolExecutor(max_workers=4) as pool:
        submitted = list(pool.map(lambda _: workflow.submit_research(value), range(4)))
    assert len({run.id for run, _ in submitted}) == 1
    assert sum(created for _, created in submitted) == 1
    assert len(client.get("/library").json()["packs"]) == 2


def test_pack_creation_failure_rolls_back_the_research_and_submission(client, monkeypatch):
    workflow, library = client.app.state.workflow, client.app.state.library
    before = client.get("/library").json()
    original_save = library.save

    def fail(conn, table, value):
        original_save(conn, table, value)
        if table == "reference_packs":
            raise RuntimeError("Test-only failure after pack insertion")

    monkeypatch.setattr(library, "save", fail)
    with pytest.raises(RuntimeError):
        workflow.submit_research(ResearchSubmissionInput.model_validate(queued_request()))
    assert client.get("/library").json() == before
    with workflow.repository.connection() as conn:
        assert not conn.execute("SELECT 1 FROM records").fetchone()
        assert not conn.execute("SELECT 1 FROM submissions").fetchone()
        assert not conn.execute("SELECT 1 FROM research_packs").fetchone()
