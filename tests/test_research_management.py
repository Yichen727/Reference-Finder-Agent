import json
from uuid import uuid4

import pytest

from backend.schemas.models import SearchRun
from backend.services.presentation import PresentationStore
from tests.conftest import completed_run


def test_manual_title_survives_reload_without_changing_research(client, monkeypatch):
    run, _ = completed_run(client)
    url = f"/search-runs/{run['id']}"
    before = client.get(url).json()
    brief = client.get(f"/briefs/{run['brief_id']}").json()
    response = client.patch(url + "/title", json={"title": "  A Human View of AI  "})
    assert response.status_code == 200
    assert response.json() == {"id": run["id"], "title": "A Human View of AI"}
    monkeypatch.setattr(
        "backend.services.presentation.research_title",
        lambda prompt: pytest.fail("Do not regenerate a manual title"),
    )
    assert client.get("/search-runs").json()[0]["title"] == "A Human View of AI"
    store = PresentationStore(client.app.state.workflow.repository)
    store.initialize()
    assert store.title(before) == "A Human View of AI"
    assert client.get(url).json() == before
    assert client.get(f"/briefs/{run['brief_id']}").json() == brief


@pytest.mark.parametrize("title", ["", "   ", "x" * 101])
def test_invalid_title_does_not_replace_saved_title(client, title):
    run, _ = completed_run(client)
    original = client.get("/search-runs").json()[0]["title"]
    response = client.patch(f"/search-runs/{run['id']}/title", json={"title": title})
    assert response.status_code == 422
    assert client.get("/search-runs").json()[0]["title"] == original


def saved_export(client, run):
    url = f"/search-runs/{run['id']}"
    client.put(
        url + "/selection",
        json={
            "ordered_ids": [run["results"][0]["id"]],
            "expected_version": 0,
        },
    ).raise_for_status()
    export = client.post(url + "/exports/docx").json()
    path = client.app.state.workflow.settings.data_dir / "exports" / export["filename"]
    return export, path


def test_soft_delete_revokes_access_but_retains_pack_and_files_safely(client):
    run, submission = completed_run(client)
    other, _ = completed_run(client, key="other-research")
    export, path = saved_export(client, run)
    other_export, other_path = saved_export(client, other)
    original_bytes = path.read_bytes()
    original_run = client.get(f"/search-runs/{run['id']}").json()
    url = f"/search-runs/{run['id']}"
    assert client.delete(url).json() == {"id": run["id"], "deleted": True}
    assert [item["id"] for item in client.get("/search-runs").json()] == [other["id"]]
    for suffix in ["", "/presentation"]:
        assert client.get(url + suffix).status_code == 404
    assert client.patch(url + "/title", json={"title": "Hidden"}).status_code == 404
    assert client.put(url + "/selection", json={"ordered_ids": [], "expected_version": 1}).status_code == 404
    assert client.post(url + "/exports/docx").status_code == 404
    assert client.get(f"/exports/{export['id']}/download").status_code == 404
    assert client.post("/search-runs", json=submission).status_code == 404
    assert client.delete(url).status_code == 200  # Safe retry after a lost response.
    assert path.read_bytes() == original_bytes
    assert other_path.is_file()
    assert client.get(f"/exports/{other_export['id']}/download").status_code == 200
    with client.app.state.workflow.repository.connection() as connection:
        stored = connection.execute(
            "SELECT payload FROM records WHERE kind='run' AND id=?", (run["id"],)
        ).fetchone()
    assert json.loads(stored["payload"]) == original_run
    client.app.state.workflow.repository.initialize()
    assert client.get(url).status_code == 404


def test_running_research_cannot_be_deleted_while_its_worker_is_active(client):
    run, _ = completed_run(client)
    record = SearchRun.model_validate(run)
    record.status = "running"
    client.app.state.workflow.repository.put("run", record)
    response = client.delete(f"/search-runs/{run['id']}")
    assert response.status_code == 409 and response.json()["error"]["code"] == "research_busy"
    assert client.get(f"/search-runs/{run['id']}").status_code == 200


def test_management_endpoints_reject_unknown_research(client):
    url = f"/search-runs/{uuid4()}"
    assert client.delete(url).status_code == 404
    assert client.patch(url + "/title", json={"title": "A New Name"}).status_code == 404
