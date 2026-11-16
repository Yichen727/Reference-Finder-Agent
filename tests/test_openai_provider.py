from types import SimpleNamespace

import httpx
import openai
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.main import create_app
from backend.schemas.models import QueryStrand, StrategyDraft


@pytest.fixture
def fake_model(monkeypatch):
    calls = []
    draft = StrategyDraft(
        summary="Research plan",
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
            return SimpleNamespace(status="completed", output_parsed=draft)

    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    return calls, FakeClient


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
