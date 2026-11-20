"""Exercise the real access gate without OpenAI calls or private configuration."""

from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from backend.core.config import ROOT
from frontend import access
from tests.test_frontend import bridge as bridge

CODE = "offline-access-code"


@pytest.fixture
def locked_workspace(bridge, monkeypatch):
    # Restore the gate disabled by the existing unlocked-UI fixture.
    monkeypatch.setattr(access, "require_access", REAL_GATE)
    monkeypatch.setattr(access, "configured_access_code", lambda: CODE)
    return bridge


REAL_GATE = access.require_access


def open_app():
    return AppTest.from_file(str(ROOT / "frontend" / "app.py"), default_timeout=15).run()


def unlock(app, code=CODE):
    app.text_input(key=access.INPUT_KEY).input(code)
    next(button for button in app.button if button.label == "Unlock").click().run()
    assert not app.exception


def test_locked_session_cannot_load_data_even_with_a_saved_research_url(locked_workspace):
    app = AppTest.from_file(str(ROOT / "frontend" / "app.py"), default_timeout=15)
    app.query_params["run"] = "saved-research"
    app.query_params["view"] = "favorites"
    app.run()
    assert not app.exception
    assert not locked_workspace["requests"]
    assert not app.sidebar.button and not app.text_area
    assert app.text_input[0].proto.type == app.text_input[0].proto.PASSWORD
    assert not app.get("dialog")[0].proto.dialog.dismissible
    unlock(app, "wrong-code")
    assert any("Incorrect security code" in item.value for item in app.error)
    assert not locked_workspace["requests"] and not app.text_area


def test_correct_code_unlocks_only_this_session_and_survives_reruns(locked_workspace):
    app = open_app()
    unlock(app)
    assert any(item.label == "Describe your creative idea" for item in app.text_area)
    assert locked_workspace["requests"]
    assert app.session_state[access.GRANT_KEY] != CODE
    assert access.INPUT_KEY not in app.session_state or not app.session_state[access.INPUT_KEY]
    app.run()
    assert not app.exception and not app.get("dialog")
    fresh = open_app()
    assert fresh.get("dialog") and not fresh.text_area


def test_code_change_relocks_an_existing_session(locked_workspace, monkeypatch):
    app = open_app()
    unlock(app)
    locked_workspace["requests"].clear()
    monkeypatch.setattr(access, "configured_access_code", lambda: "replacement-code")
    app.run()
    assert not locked_workspace["requests"] and not app.text_area
    unlock(app)
    assert app.error and not locked_workspace["requests"]
    unlock(app, "replacement-code")
    assert app.text_area and app.session_state[access.GRANT_KEY]


def test_failed_attempts_have_a_cooldown_without_sleeping(locked_workspace, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(access, "monotonic", lambda: clock[0])
    app = open_app()
    for _ in range(access.ATTEMPT_LIMIT):
        unlock(app, "incorrect")
    assert next(button for button in app.button if button.label == "Unlock").disabled
    assert app.warning and not locked_workspace["requests"]
    clock[0] += access.COOLDOWN_SECONDS + 1
    next(button for button in app.button if button.label == "Try Again").click().run()
    unlock(app)
    assert app.text_area


def test_missing_code_fails_closed_before_loading_the_workspace(locked_workspace, monkeypatch):
    monkeypatch.setattr(access, "configured_access_code", lambda: None)
    app = open_app()
    assert not app.exception and not app.text_area and not app.get("dialog")
    assert any("APP_ACCESS_CODE" in error.value for error in app.error)
    assert not locked_workspace["requests"]


@pytest.mark.parametrize("code", [None, "incorrect-code"])
def test_cloud_gate_runs_before_backend_startup(monkeypatch, code):
    monkeypatch.setattr(access, "configured_access_code", lambda: code)

    def unexpected_startup(*args, **kwargs):
        pytest.fail("A locked cloud session must not start an API worker")

    monkeypatch.setattr("frontend.cloud_runtime.ensure_backend", unexpected_startup)
    app = AppTest.from_file(str(ROOT / "cloud" / "streamlit_app.py")).run()
    assert not app.exception and not app.text_area
    if code:
        unlock(app, "wrong-code")
        assert app.error and not app.text_area
    else:
        assert any("APP_ACCESS_CODE" in error.value for error in app.error)


@pytest.mark.parametrize("configured,expected", [(CODE, CODE), (12345678, None), ("", None)])
def test_access_code_is_private_string_configuration(monkeypatch, configured, expected):
    monkeypatch.delenv("APP_ACCESS_CODE", raising=False)
    monkeypatch.setattr("streamlit.secrets", SimpleNamespace(
        to_dict=lambda: {"APP_ACCESS_CODE": configured},
    ))
    assert access.configured_access_code() == expected


def test_environment_access_code_does_not_require_reading_private_files(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_CODE", CODE)
    monkeypatch.setattr("streamlit.secrets", None)
    assert access.configured_access_code() == CODE
