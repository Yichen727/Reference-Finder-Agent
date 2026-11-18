import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.main import create_app
from backend.schemas.models import QueryStrand, StrategyDraft
from tests.fixtures import FixtureProvider


@pytest.fixture
def fake_model(monkeypatch):
    calls = []
    draft = StrategyDraft(
        summary="Research plan",
        research_title="Crystal Palace FC Introduction Film",
        assumptions=[],
        strands=[
            QueryStrand(name="Tone", intent="Find warm storytelling", queries=["warm storytelling film"]),
        ],
    )

    class FakeClient:
        def __init__(self, **kwargs):
            self.responses = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def parse(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(status="completed", output_parsed=self.draft)

    FakeClient.draft = draft
    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    return calls, FakeClient


@pytest.mark.parametrize(
    "prompt,title",
    [
        (
            "I want to make an introduction video for Crystal Palace football club.",
            "Crystal Palace FC Introduction Film",
        ),
        (
            "Please help me create a film exploring the human side of AI through everyday stories.",
            "Human Side of AI",
        ),
        (
            "I need references for a renewable energy brand film with a hopeful tone.",
            "Renewable Energy Brand Film",
        ),
    ],
)
def test_same_planning_call_returns_and_persists_topic_title(settings, fake_model, prompt, title):
    calls, fake_class = fake_model
    fake_class.draft = fake_class.draft.model_copy(update={"research_title": title})
    with TestClient(create_app(settings)) as client:
        client.app.state.workflow.provider.search = FixtureProvider().search
        client.app.state.thumbnails.for_run = lambda run: {}
        brief = client.post("/briefs", json={"concept": prompt, "requested_count": 4}).json()
        planned = client.post(f"/briefs/{brief['id']}/strategy")
        assert planned.status_code == 200
        assert planned.json()["research_title"] == title
        assert json.loads(calls[0]["input"])["concept"] == prompt
        assert "Return research_title" in calls[0]["instructions"]
        submitted = client.post(
            "/search-runs",
            json={"brief_id": brief["id"], "strategy": planned.json(), "idempotency_key": "topic-title-test"},
        )
        assert submitted.status_code == 202
        run = submitted.json()
        saved = client.app.state.workflow.repository.get("run", run["id"])
        assert saved["strategy"]["research_title"] == title
        for _ in range(2):
            assert client.get("/search-runs").json()[0]["title"] == title
            assert client.get(f"/search-runs/{run['id']}/presentation").json()["title"] == title
        assert client.get(f"/briefs/{brief['id']}").json()["concept"] == prompt
        renamed = client.patch(f"/search-runs/{run['id']}/title", json={"title": "My Curated Film Research"})
        assert renamed.status_code == 200
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get("/search-runs").json()[0]["title"] == "My Curated Film Research"
    assert len(calls) == 1  # Reading, renaming and restarting never generate titles again.


@pytest.mark.parametrize("title", ["", "   "])
def test_missing_generated_title_is_reported_without_excerpting_the_prompt(settings, fake_model, title):
    calls, fake_class = fake_model
    fake_class.draft = StrategyDraft(**{**fake_class.draft.model_dump(), "research_title": title})
    with TestClient(create_app(settings)) as client:
        brief = client.post("/briefs", json={"concept": "I want to make an introduction video"}).json()
        response = client.post(f"/briefs/{brief['id']}/strategy")
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "invalid_model_output"
        assert client.get("/search-runs").json() == []
    assert len(calls) == 1


def test_generated_title_whitespace_is_normalized_without_truncating_meaning():
    title = "  Crystal Palace\n FC Introduction Film  "
    draft = StrategyDraft(
        summary="Research plan",
        research_title=title,
        strands=[QueryStrand(name="Film", intent="Club identity", queries=["club film"])],
    )
    assert draft.research_title == "Crystal Palace FC Introduction Film"


def test_openai_structured_check_has_no_search_claim_or_secret(settings, fake_model):
    settings.openai_api_key = SecretStr("test-secret-never-return-this")
    settings.openai_model = "configured-test-model"
    with TestClient(create_app(settings)) as client:
        health = client.get("/health")
        assert settings.openai_api_key.get_secret_value() not in health.text
        response = client.post("/providers/openai/check")
        assert response.status_code == 200
        assert response.json()["structured_output"] is True
        assert response.json()["live_search_verified"] is False
        assert settings.openai_api_key.get_secret_value() not in response.text
        calls, _ = fake_model
        assert calls[0]["store"] is False
        assert calls[0]["text_format"] is StrategyDraft
        # A model check must not write credentials or sample data into persistence.
        database = (settings.data_dir / "reference_finder.sqlite3").read_bytes()
        assert b"test-secret-never-return-this" not in database


@pytest.mark.parametrize("failure, expected", [("auth", 502), ("rate", 429), ("timeout", 504)])
def test_openai_errors_are_sanitized(settings, fake_model, monkeypatch, failure, expected):
    settings.openai_api_key = SecretStr("test-secret")
    settings.openai_model = "configured-test-model"
    _, fake_class = fake_model
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")

    def fail(**kwargs):
        if failure == "timeout":
            raise openai.APITimeoutError(request=request)
        response = httpx.Response(401 if failure == "auth" else 429, request=request)
        cls = openai.AuthenticationError if failure == "auth" else openai.RateLimitError
        raise cls("sensitive upstream detail", response=response, body={"secret": "test-secret"})

    monkeypatch.setattr(fake_class, "parse", staticmethod(fail))
    with TestClient(create_app(settings)) as client:
        response = client.post("/providers/openai/check")
        assert response.status_code == expected
        assert "test-secret" not in response.text and "sensitive upstream detail" not in response.text
