from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import pytest
from docx import Document
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from backend.core.config import Settings
from backend.main import create_app
from backend.schemas.models import SearchRun
from tests.conftest import completed_run
from tests.fixtures import FixtureProvider


def test_selection_export_roundtrip_and_restart(client, settings):
    run, _ = completed_run(client, count=15)
    assert len(run["results"]) == 15
    assert all(not item["is_mock"] for item in run["results"])
    chosen = [run["results"][2], run["results"][0]]
    annotation = {**chosen[0]["annotation"], "what_to_borrow": "My edited creative principle"}
    response = client.put(
        f"/search-runs/{run['id']}/selection",
        json={
            "ordered_ids": [item["id"] for item in chosen],
            "annotation_overrides": {chosen[0]["id"]: annotation},
            "expected_version": 0,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 1
    export = client.post(f"/search-runs/{run['id']}/exports/docx")
    assert export.status_code == 201
    record = export.json()
    download = client.get(f"/exports/{record['id']}/download")
    assert download.status_code == 200
    assert "wordprocessingml" in download.headers["content-type"]
    content = download.content
    doc = Document(BytesIO(content))
    paragraphs = "\n".join(p.text for p in doc.paragraphs)
    assert "My edited creative principle" in paragraphs
    assert "My edited creative principle" in paragraphs
    assert paragraphs.index(chosen[0]["title"]) < paragraphs.index(chosen[1]["title"])
    with ZipFile(BytesIO(content)) as archive:
        assert b"hyperlink" in archive.read("word/document.xml")
        assert chosen[0]["source_url"].encode() in archive.read("word/_rels/document.xml.rels")
    # Reopen the same disk-backed app to prove state survives a fresh service instance.
    with TestClient(create_app(settings)) as restarted:
        restored = restarted.get(f"/search-runs/{run['id']}").json()
        assert restored["selection"]["ordered_ids"] == [item["id"] for item in chosen]
        assert restored["selection"]["annotation_overrides"][chosen[0]["id"]] == annotation
        assert restarted.get(f"/exports/{record['id']}/download").content == content


@pytest.mark.parametrize(
    "payload",
    [
        {"concept": "private marker", "requested_count": 16},
        {"concept": "   "},
        {"concept": "private marker", "preferred_sources": []},
    ],
)
def test_invalid_brief_does_not_echo_input(client, payload):
    response = client.post("/briefs", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "private marker" not in response.text
    missing = client.get("/unknown-endpoint")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "http_error"


def test_service_honours_lower_configured_limit(settings):
    settings.max_references = 2
    with TestClient(create_app(settings)) as client:
        client.app.state.workflow.provider = FixtureProvider()
        assert client.post("/briefs", json={"concept": "Film", "requested_count": 3}).status_code == 422
        run, _ = completed_run(client, count=2)
        assert len(run["results"]) == 2


def test_idempotency_and_conflicting_payload(client):
    run, request = completed_run(client)
    repeated = client.post("/search-runs", json=request)
    assert repeated.json()["id"] == run["id"]
    assert len(client.get("/search-runs").json()) == 1
    request["strategy"]["summary"] = "Changed request"
    response = client.post("/search-runs", json=request)
    assert response.status_code == 409


def test_invalid_selection_conflicts_and_empty_export(client):
    run, _ = completed_run(client)
    other, _ = completed_run(client, key="another-run")
    url = f"/search-runs/{run['id']}/selection"
    item = run["results"][0]
    assert client.post(f"/search-runs/{run['id']}/exports/docx").status_code == 422
    for ids in [[item["id"], item["id"]], [other["results"][0]["id"]], [str(uuid4())] * 16]:
        assert client.put(url, json={"ordered_ids": ids, "expected_version": 0}).status_code == 422
    bad_override = {
        "ordered_ids": [],
        "annotation_overrides": {item["id"]: item["annotation"]},
        "expected_version": 0,
    }
    assert client.put(url, json=bad_override).status_code == 422
    good = {"ordered_ids": [item["id"]], "expected_version": 0}
    assert client.put(url, json=good).status_code == 200
    assert client.put(url, json=good).status_code == 409
    assert client.put(url, json={"ordered_ids": [], "expected_version": 1}).status_code == 200
    assert client.post(f"/search-runs/{run['id']}/exports/docx").status_code == 422


def test_restart_marks_pending_job_interrupted(client, settings):
    run, _ = completed_run(client)
    pending = SearchRun.model_validate(run)
    pending.status = "running"
    client.app.state.workflow.repository.put("run", pending)
    with TestClient(create_app(settings)) as restarted:
        response = restarted.get(f"/search-runs/{run['id']}").json()
        assert response["status"] == "interrupted"
        assert any("restarted" in warning for warning in response["warnings"])


def test_provider_output_missing_evidence_fails_safely(client):
    workflow = client.app.state.workflow
    original = workflow.provider.search

    def corrupt(brief, strategy, progress=None):
        outcome = original(brief, strategy)
        outcome.evidence = []
        return outcome

    workflow.provider.search = corrupt
    brief = client.post("/briefs", json={"concept": "A film"}).json()
    strategy = client.post(f"/briefs/{brief['id']}/strategy").json()
    request = {"brief_id": brief["id"], "strategy": strategy, "idempotency_key": "corrupt"}
    response = client.post("/search-runs", json=request)
    result = client.get(f"/search-runs/{response.json()['id']}").json()
    assert result["status"] == "failed"
    assert result["results"] == []
    assert result["error_code"] == "run_failed"


def test_missing_credentials_require_real_configuration(settings):
    settings.openai_api_key = SecretStr("")
    with TestClient(create_app(settings)) as client:
        health = client.get("/health").json()
        assert health["mode"] == "live" and not health["openai_configured"]
        assert not health["live_search_ready"]
        assert client.post("/providers/openai/check").status_code == 503
        brief = client.post("/briefs", json={"concept": "Real brief"}).json()
        response = client.post(f"/briefs/{brief['id']}/strategy")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "openai_not_configured"


def test_application_has_no_demonstration_mode(client):
    assert client.get("/health").json()["capabilities"]["mock_workflow"] is False
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_mode="mock")


def test_export_checks_saved_selection_version_without_breaking_old_requests(client):
    run, _ = completed_run(client)
    url = f"/search-runs/{run['id']}"
    selected = [run["results"][0]["id"]]
    assert (
        client.put(url + "/selection", json={"ordered_ids": selected, "expected_version": 0}).status_code
        == 200
    )
    assert (
        client.put(url + "/selection", json={"ordered_ids": selected, "expected_version": 1}).status_code
        == 200
    )
    response = client.post(url + "/exports/docx", json={"expected_selection_version": 1})
    assert response.status_code == 409
    assert client.post(url + "/exports/docx", json={"expected_selection_version": 2}).status_code == 201
    assert client.post(url + "/exports/docx").status_code == 201


def test_legacy_demonstrations_remain_readable_but_cannot_be_reused(client):
    run, request = completed_run(client)
    archived = SearchRun.model_validate(run)
    archived.mode = "mock"
    client.app.state.workflow.repository.put("run", archived)
    assert client.get(f"/search-runs/{run['id']}").status_code == 200
    assert (
        client.put(
            f"/search-runs/{run['id']}/selection",
            json={
                "ordered_ids": [run["results"][0]["id"]],
                "expected_version": 0,
            },
        ).status_code
        == 409
    )
    assert client.post(f"/search-runs/{run['id']}/exports/docx").status_code == 409
    request["strategy"]["is_mock"] = True
    assert client.post("/search-runs", json=request).status_code == 409
