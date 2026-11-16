import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.main import create_app
from tests.fixtures import FixtureProvider


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, openai_api_key="test-only-key", openai_model="")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as instance:
        instance.app.state.workflow.provider = FixtureProvider()
        instance.app.state.thumbnails.for_run = lambda run: {}
        yield instance


def completed_run(client, *, count=3, language="en", key="submission-one"):
    brief_response = client.post(
        "/briefs",
        json={
            "concept": "A warm film about sustainable ideas",
            "requested_count": count,
            "language": language,
        },
    )
    assert brief_response.status_code == 201, brief_response.text
    brief = brief_response.json()
    strategy_response = client.post(f"/briefs/{brief['id']}/strategy")
    assert strategy_response.status_code == 200, strategy_response.text
    request = {"brief_id": brief["id"], "strategy": strategy_response.json(), "idempotency_key": key}
    response = client.post("/search-runs", json=request)
    assert response.status_code == 202, response.text
    run = client.get(f"/search-runs/{response.json()['id']}").json()
    assert run["status"] == "completed", run
    return run, request
