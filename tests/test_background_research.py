"""Navigation never owns or cancels the backend's planning/search worker."""

import copy
from threading import Event, Thread

import pytest
from streamlit.testing.v1 import AppTest

from backend.core.errors import AppError
from backend.schemas.models import ResearchActionInput, ResearchSubmissionInput
from frontend.api_client import APIClient
from tests.conftest import completed_run
from tests.fixtures import FixtureProvider
from tests.test_frontend import APP, button, card_count, pick, widget
from tests.test_frontend import bridge as bridge


class BlockingProvider(FixtureProvider):
    def __init__(self, phase):
        self.phase = phase
        self.started, self.release = Event(), Event()
        self.planning_calls = self.search_calls = 0

    def pause(self, phase):
        if phase == self.phase:
            self.started.set()
            if not self.release.wait(30):
                raise AssertionError("Background test worker was not released")

    def strategy(self, brief):
        self.planning_calls += 1
        self.pause("planning")
        return super().strategy(brief)

    def search(self, brief, strategy, progress=None, *, excluded_works=None):
        self.search_calls += 1
        if progress:
            progress("searching_1_of_8")
        self.pause("search")
        return super().search(brief, strategy, progress, excluded_works=excluded_works)


def create_task(workflow, parent_id, operation):
    payload = {
        "brief": {"concept": "A cinematic football fan film", "requested_count": 4},
        "idempotency_key": f"background-{operation}",
    }
    if operation == "search":
        return workflow.submit_research(ResearchSubmissionInput(**payload))[0], payload
    payload["action"] = operation
    return workflow.submit_action(parent_id, ResearchActionInput(**payload))[0], payload


@pytest.mark.parametrize("operation", ["search", "add", "regenerate"])
@pytest.mark.parametrize("phase", ["planning", "search"])
def test_switching_research_and_home_does_not_stop_any_backend_stage(bridge, client, operation, phase):
    parent, _ = completed_run(client, count=4, key="parent")
    other, _ = completed_run(client, count=4, key="other")
    workflow = client.app.state.workflow
    provider = BlockingProvider(phase)
    workflow.provider = provider
    task, payload = create_task(workflow, parent["id"], operation)
    target_id = task.id if operation == "search" else parent["id"]
    worker = Thread(target=workflow.execute, args=(task.id,), daemon=True)
    worker.start()
    try:
        assert provider.started.wait(5)
        assert workflow.run(task.id).status == "running"
        app = AppTest.from_file(str(APP), default_timeout=15)
        app.query_params["run"] = target_id
        app.run()
        assert not app.exception and app.status
        assert card_count(app) == (0 if operation == "search" else 4)
        widget(app.button, f"research_{other['id']}").click().run()
        assert not app.exception and app.session_state["run_id"] == other["id"]
        pick(app, other, 0)
        draft = copy.deepcopy(app.session_state["pack_drafts"][other["id"]])
        assert workflow.run(task.id).status == "running"
        button(app, "New Research").click().run()
        assert not app.exception and "run_id" not in app.session_state
        assert workflow.run(task.id).status == "running"
        # Replaying the accepted request while the worker runs schedules no second job.
        path = "/search-runs" if operation == "search" else f"/search-runs/{parent['id']}/actions"
        replay = client.post(path, json=payload)
        assert replay.status_code == 202 and replay.json()["id"] == task.id
        provider.release.set()
        worker.join(10)
        assert not worker.is_alive()
        assert workflow.run(task.id).status == "completed"
        assert provider.planning_calls == provider.search_calls == 1
        app.run()
        assert "run_id" not in app.session_state  # Completion does not hijack the current page.
        assert app.session_state["pack_drafts"][other["id"]] == draft
        widget(app.button, f"research_{target_id}").click().run()
        assert not app.exception
        assert card_count(app) == (8 if operation == "add" else 4)
        assert app.session_state["run_id"] == (parent["id"] if operation == "add" else task.id)
        assert app.session_state["pack_drafts"][other["id"]] == draft
    finally:
        provider.release.set()
        worker.join(5)


@pytest.mark.parametrize("operation", ["search", "add"])
def test_late_poll_response_cannot_replace_the_newly_selected_research(
    bridge, client, monkeypatch, operation
):
    parent, _ = completed_run(client, count=4, key="late-parent")
    other, _ = completed_run(client, count=4, key="late-other")
    workflow = client.app.state.workflow
    task, _ = create_task(workflow, parent["id"], operation)
    target = task.id if operation == "search" else parent["id"]
    app = AppTest.from_file(str(APP), default_timeout=15)
    app.query_params["run"] = target
    app.run()
    assert not app.exception
    workflow.execute(task.id)
    real_request = APIClient.request

    def late_response(self, method, path, *args, **kwargs):
        response = real_request(self, method, path, *args, **kwargs)
        if method == "GET" and path == f"/search-runs/{task.id}":
            app.session_state["run_id"] = other["id"]
            app.session_state["run"] = other
        return response

    monkeypatch.setattr(APIClient, "request", late_response)
    app.run()
    assert not app.exception and app.session_state["run_id"] == other["id"]
    assert app.session_state["run"]["id"] == other["id"]


def test_background_submission_is_persisted_before_any_model_call(client):
    workflow = client.app.state.workflow
    calls = []

    def strategy(brief):
        calls.append("planning")
        return FixtureProvider().strategy(brief)

    workflow.provider.strategy = strategy
    request = ResearchSubmissionInput(
        brief={"concept": "An independent background research", "requested_count": 4},
        idempotency_key="background-planning-once",
    )
    queued, created = workflow.submit_research(request)
    assert created and queued.status == "queued" and queued.planning_pending
    assert not calls
    assert workflow.brief(queued.brief_id).concept == request.brief.concept
    assert workflow.submit_research(request)[1] is False
    workflow.execute(queued.id)
    assert workflow.run(queued.id).status == "completed" and calls == ["planning"]


def test_background_planning_failure_is_saved_without_leaving_a_running_job(bridge, client):
    def fail(*args, **kwargs):
        raise AppError("test_failure", "Test-only planning failure.", 502)

    client.app.state.workflow.provider.strategy = fail
    response = client.post("/search-runs", json={
        "brief": {"concept": "A failed background planning request", "requested_count": 4},
        "idempotency_key": "failed-background-plan",
    })
    assert response.status_code == 202
    run = client.get(f"/search-runs/{response.json()['id']}").json()
    assert run["status"] == "failed" and run["error_code"] == "test_failure"
    restored = AppTest.from_file(str(APP), default_timeout=15)
    restored.query_params["run"] = run["id"]
    restored.run()
    assert not restored.exception and not restored.status
    assert any("Test-only planning failure." in item.value for item in restored.error)
