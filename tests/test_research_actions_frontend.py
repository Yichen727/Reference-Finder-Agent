"""Inline research updates preserve browsing and unsaved pack drafts."""

import copy

import pytest
from streamlit.testing.v1 import AppTest

from backend.core.errors import AppError
from backend.schemas.models import ResearchActionInput
from frontend.api_client import APIClient, APIError
from tests.fixtures import FixtureProvider
from tests.test_frontend import (
    APP,
    area,
    button,
    card_count,
    details,
    launch,
    open_app,
    pick,
    reorder,
    widget,
)
from tests.test_frontend import (
    bridge as bridge,
)


def editor(app, mode="Add"):
    button(app, "Add References" if mode == "Add" else "Regenerate").click().run()
    assert not app.exception
    return app


def submit_editor(app, mode="Add", count=4):
    number = next(item for item in app.number_input if item.label == (
        "Additional References" if mode == "Add" else "Reference Count"
    ))
    number.set_value(count)
    button(app, "Find More References" if mode == "Add" else "Regenerate Results").click().run()
    assert not app.exception
    return app


def test_both_header_actions_open_one_prefilled_inline_panel(bridge):
    app = launch(open_app(), count=4)
    run = app.session_state["run"]
    assert not any(item.label == "Creative Brief" for item in app.text_area)
    editor(app)
    assert area(app, "Creative Brief").value == app.session_state["brief"]["concept"]
    sources = widget(app.multiselect, f"research_edit_{run['id']}_sources")
    assert sources.value == run["strategy"]["preferred_sources"]
    area(app, "Creative Brief").input("Edited brief with a new direction").run()
    app.radio[0].set_value("Regenerate").run()
    assert area(app, "Creative Brief").value == "Edited brief with a new direction"
    assert any(item.label == "Reference Count" and item.value == 4 for item in app.number_input)
    assert card_count(app) == 4
    button(app, "Cancel").click().run()
    assert not any(item.label == "Creative Brief" for item in app.text_area)
    assert app.session_state["run"]["id"] == run["id"]
    editor(app, "Regenerate")
    assert app.radio[0].value == "Regenerate"
    assert area(app, "Creative Brief").value == app.session_state["brief"]["concept"]


def test_add_keeps_unsaved_annotations_exclusions_filters_and_pack_order(bridge):
    app = launch(open_app(), count=4)
    run = copy.deepcopy(app.session_state["run"])
    pick(app, run, 0)
    pick(app, run, 2)
    reorder(app, run, [run["results"][2]["id"], run["results"][0]["id"]])
    draft = app.session_state["pack_drafts"][run["id"]]
    draft["annotations"][run["results"][0]["id"]]["what_to_borrow"] = "Keep this unsaved annotation."
    draft["rejected_ids"] = [run["results"][1]["id"]]
    draft["rejection_notes"] = "Keep this unsaved note."
    before = copy.deepcopy(draft)
    widget(app.multiselect, f"source_filter_{run['id']}").set_value([run["results"][0]["platform"]]).run()
    app.run()
    editor(app)
    area(app, "Creative Brief").input("More upbeat environmental films").run()
    submit_editor(app)
    updated = app.session_state["run"]
    assert updated["id"] == run["id"] and len(updated["results"]) == 8
    after = app.session_state["pack_drafts"][run["id"]]
    assert all(after[key] == value for key, value in before.items() if key != "annotations")
    assert all(after["annotations"][key] == value for key, value in before["annotations"].items())
    assert len(after["annotations"]) == 8
    assert card_count(app) == 7
    assert widget(app.multiselect, f"source_filter_{run['id']}").value == [run["results"][0]["platform"]]
    assert app.query_params["run"] == run["id"]
    assert sum(method == "POST" and path.endswith("/actions") for method, path, _ in bridge["requests"]) == 1
    app.run()
    assert len(app.session_state["run"]["results"]) == 8
    new_item = updated["results"][-1]
    widget(app.button, f"pick_action_{run['id']}_{new_item['id']}").click().run()
    details(app, new_item)
    button(app, "Done").click().run()
    button(app, "Export Reference Pack").click().run()
    assert not app.exception and app.session_state["pack_downloads"][run["id"]]["format"] == "pptx"


def test_regenerate_opens_new_research_and_keeps_original_draft(bridge, client):
    app = launch(open_app(), count=4)
    original = copy.deepcopy(app.session_state["run"])
    pick(app, original, 0)
    before = copy.deepcopy(app.session_state["pack_drafts"][original["id"]])
    editor(app, "Regenerate")
    area(app, "Creative Brief").input("A bold new creative direction").run()
    submit_editor(app, "Regenerate")
    version = app.session_state["run"]
    assert version["id"] != original["id"] and version["parent_run_id"] == original["id"]
    assert app.session_state["brief"]["concept"] == "A bold new creative direction"
    assert app.session_state["pack_drafts"][original["id"]] == before
    assert app.session_state["pack_drafts"][version["id"]]["ordered_ids"] == []
    widget(app.button, f"research_{original['id']}").click().run()
    assert app.session_state["pack_drafts"][original["id"]] == before
    assert client.get(f"/search-runs/{original['id']}").json() == original


@pytest.mark.parametrize("field", ["brief", "sources"])
def test_editor_validation_keeps_existing_cards_and_does_not_search(bridge, field):
    app = launch(open_app(), count=4)
    editor(app)
    if field == "brief":
        area(app, "Creative Brief").input(" ").run()
    else:
        widget(app.multiselect, f"research_edit_{app.session_state['run_id']}_sources").set_value([]).run()
    button(app, "Find More References").click().run()
    assert not app.exception and card_count(app) == 4
    assert app.error
    assert not any(method == "POST" and path.endswith("/actions") for method, path, _ in bridge["requests"])


def test_lost_submission_response_retries_same_key_without_duplicate_batch(bridge, monkeypatch):
    app = launch(open_app(), count=4)
    run_id = app.session_state["run"]["id"]
    real = APIClient.request
    calls = []

    def lose_response(self, method, path, payload=None, **kwargs):
        response = real(self, method, path, payload, **kwargs)
        if method == "POST" and path.endswith("/actions"):
            calls.append(copy.deepcopy(payload))
            if len(calls) == 1:
                raise APIError("Response lost after submission.", status=504)
        return response

    monkeypatch.setattr(APIClient, "request", lose_response)
    editor(app)
    submit_editor(app)
    assert app.error and card_count(app) == 4
    assert button(app, "Find More References").disabled
    button(app, "Retry Submission").click().run()
    assert not app.exception
    assert len(app.session_state["run"]["results"]) == 8
    assert calls[0] == calls[1]
    assert app.session_state["run"]["appended_batch_ids"]
    assert app.session_state["run"]["id"] == run_id


def test_failed_search_keeps_editor_parameters_for_a_new_submission(bridge, client):
    app = launch(open_app(), count=4)
    original = copy.deepcopy(app.session_state["run"])

    def fail(*args, **kwargs):
        raise AppError("test_failure", "Test-only provider failure.", 502)

    client.app.state.workflow.provider.search = fail
    editor(app)
    area(app, "Creative Brief").input("Keep my revised direction").run()
    submit_editor(app)
    assert not app.exception and app.error
    assert area(app, "Creative Brief").value == "Keep my revised direction"
    assert card_count(app) == 4 and app.session_state["run"] == original
    client.app.state.workflow.provider = FixtureProvider()
    submit_editor(app)
    assert len(app.session_state["run"]["results"]) == 8


def test_url_restoration_recovers_pending_update_and_preserves_saved_pack(bridge, client):
    app = launch(open_app(), count=4)
    original = app.session_state["run"]
    pick(app, original, 0)
    button(app, "Export Reference Pack").click().run()
    saved = client.get(f"/search-runs/{original['id']}").json()["selection"]
    request = ResearchActionInput(
        action="add", brief={"concept": "A pending additional direction", "requested_count": 4},
        idempotency_key="restore-update",
    )
    task, _ = client.app.state.workflow.submit_action(original["id"], request)
    restored = AppTest.from_file(str(APP), default_timeout=15)
    restored.query_params["run"] = original["id"]
    restored.run()
    assert not restored.exception and card_count(restored) == 4
    assert restored.session_state["research_operations"][original["id"]] == task.id
    assert button(restored, "Add References").disabled
    assert restored.status
    pick(restored, original, 2)
    expected_order = [*saved["ordered_ids"], original["results"][2]["id"]]
    client.app.state.workflow.execute(task.id)
    restored.run()
    assert not restored.exception and card_count(restored) == 8
    assert restored.session_state["pack_drafts"][original["id"]]["ordered_ids"] == expected_order


def test_definitive_submission_rejection_keeps_editor_editable(bridge, monkeypatch):
    app = launch(open_app(), count=4)
    real = APIClient.request

    def reject(self, method, path, payload=None, **kwargs):
        if method == "POST" and path.endswith("/actions"):
            raise APIError("The search service is not configured.", status=503)
        return real(self, method, path, payload, **kwargs)

    monkeypatch.setattr(APIClient, "request", reject)
    editor(app)
    area(app, "Creative Brief").input("Keep the rejected submission editable").run()
    submit_editor(app)
    assert app.error and not app.exception
    assert not area(app, "Creative Brief").disabled
    assert area(app, "Creative Brief").value == "Keep the rejected submission editable"
    assert not button(app, "Cancel").disabled
    assert not button(app, "Find More References").disabled
