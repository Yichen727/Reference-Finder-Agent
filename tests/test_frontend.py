import copy
from io import BytesIO
from pathlib import Path

import pytest
from docx import Document
from streamlit.testing.v1 import AppTest

from backend.schemas.models import ApprovedThumbnail, SearchRun
from frontend.api_client import APIClient, APIError

APP = Path(__file__).resolve().parents[1] / "frontend" / "app.py"


def button(app, label):
    return next(item for item in app.button if item.label == label)


def widget(elements, key):
    return next(item for item in elements if item.key == key)


def area(app, label):
    return next(item for item in app.text_area if item.label == label)


@pytest.fixture
def bridge(client, monkeypatch):
    state = {"requests": [], "fail_export": False, "lose_submission": False, "race_export": False}

    def request(self, method, path, payload=None, *, binary=False):
        state["requests"].append((method, path, copy.deepcopy(payload)))
        if method == "POST" and path.endswith("/exports/docx"):
            if state["fail_export"]:
                state["fail_export"] = False
                raise APIError("Document generation failed.", code="export_failed", status=500)
            if state["race_export"]:
                state["race_export"] = False
                run_url = path.removesuffix("/exports/docx")
                selection = client.get(run_url).json()["selection"]
                client.put(
                    run_url + "/selection",
                    json={
                        "ordered_ids": selection["ordered_ids"],
                        "expected_version": selection["version"],
                    },
                )
        response = client.request(method, path, json=payload)
        if state["lose_submission"] and method == "POST" and path == "/search-runs":
            state["lose_submission"] = False
            raise APIError("Response lost after submission.", status=504)
        if response.is_error:
            detail = response.json()["error"]
            raise APIError(detail["message"], code=detail["code"], status=response.status_code)
        return response.content if binary else response.json()

    monkeypatch.setattr(APIClient, "request", request)
    return state


def open_app():
    app = AppTest.from_file(str(APP), default_timeout=15).run()
    assert not app.exception
    return app


def launch(app, concept="A warm film about a shared future", *, count=3):
    area(app, "Describe your creative idea").input(concept)
    app.number_input[0].set_value(count)
    button(app, "Find References").click().run()
    assert not app.exception
    return app


def pick(app, run, index):
    widget(app.checkbox, f"pick_{run['id']}_{run['results'][index]['id']}").check().run()


def details(app, item):
    widget(app.button, f"details_{item['id']}").click().run()
    assert not app.exception


def edit_borrow(app, run, item, value):
    details(app, item)
    widget(app.text_area, f"edit_{run['id']}_{item['id']}_what_to_borrow").input(value).run()
    button(app, "Done").click().run()
    assert not app.exception


def test_home_is_simple_and_defaults_launch_with_one_click(bridge, client):
    app = open_app()
    assert app.title[0].value == "Reference Finder"
    assert all(not item.proto.expanded for item in app.expander)
    assert [item.label for item in app.main.button if item.proto.type == "primary"] == ["Find References"]
    area(app, "Describe your creative idea").input("An optimistic character-led campaign")
    button(app, "Find References").click().run()
    assert not app.exception
    run = app.session_state["run"]
    assert len(run["results"]) == 12
    assert run["strategy"]["strands"]
    assert run["mode"] == "live"
    assert sum(method == "POST" and path.endswith("/strategy") for method, path, _ in bridge["requests"]) == 1
    assert sum(method == "POST" and path == "/search-runs" for method, path, _ in bridge["requests"]) == 1
    app.run()
    pick(app, run, 0)
    assert len(client.get("/search-runs").json()) == 1
    assert sum(method == "POST" and path == "/search-runs" for method, path, _ in bridge["requests"]) == 1
    assert not app.get("imgs")


def test_export_automatically_saves_latest_order_and_annotations(bridge, client):
    app = launch(open_app())
    run = app.session_state["run"]
    first, second = run["results"][:2]
    pick(app, run, 0)
    pick(app, run, 1)
    edit_borrow(app, run, first, "My revised creative principle")
    widget(app.button, f"down_{first['id']}").click().run()
    assert app.session_state["pack_drafts"][run["id"]]["ordered_ids"] == [second["id"], first["id"]]
    widget(app.button, f"up_{first['id']}").click().run()
    widget(app.button, f"down_{first['id']}").click().run()
    assert not any("Save selection" in item.label for item in app.button)
    button(app, "Export Reference Pack").click().run()
    assert not app.exception
    saved = client.get(f"/search-runs/{run['id']}").json()["selection"]
    assert saved["ordered_ids"] == [second["id"], first["id"]]
    assert saved["annotation_overrides"][first["id"]]["what_to_borrow"] == "My revised creative principle"
    download = app.session_state["pack_downloads"][run["id"]]
    text = "\n".join(paragraph.text for paragraph in Document(BytesIO(download["content"])).paragraphs)
    assert "My revised creative principle" in text
    assert text.index(second["title"]) < text.index(first["title"])
    export = next(
        payload
        for method, path, payload in bridge["requests"]
        if method == "POST" and path.endswith("/exports/docx")
    )
    assert export["expected_selection_version"] == saved["version"]
    assert len(app.get("download_button")) == 1


def test_remove_and_navigation_preserve_unsaved_annotation_drafts(bridge):
    app = launch(open_app())
    run = app.session_state["run"]
    first, second = run["results"][:2]
    pick(app, run, 0)
    pick(app, run, 1)
    edit_borrow(app, run, first, "Keep this draft across navigation")
    widget(app.button, f"remove_{second['id']}").click().run()
    assert not widget(app.checkbox, f"pick_{run['id']}_{second['id']}").value
    button(app, "New Research").click().run()
    launch(app, concept="A different idea")
    next(item for item in app.selectbox if item.label == "Recent Research").set_value(run["id"]).run()
    button(app, "Open Research").click().run()
    assert app.session_state["run_id"] == run["id"]
    assert widget(app.checkbox, f"pick_{run['id']}_{first['id']}").value
    details(app, first)
    assert (
        widget(app.text_area, f"edit_{run['id']}_{first['id']}_what_to_borrow").value
        == "Keep this draft across navigation"
    )


def test_search_options_only_expose_sources_count_and_constraints(bridge, client):
    app = open_app()
    assert [item.label for item in app.text_area] == ["Describe your creative idea", "Creative Constraints"]
    assert [item.label for item in app.multiselect] == ["Sources"]
    assert [item.label for item in app.number_input] == ["Reference Count"]
    assert not app.text_input and not app.checkbox
    area(app, "Creative Constraints").input("One location, no dialogue, avoid aerial shots")
    launch(app, concept="A playful 30-second film for young commuters")
    assert not app.exception
    brief = app.session_state["brief"]
    assert brief["constraints"] == "One location, no dialogue, avoid aerial shots"
    submitted = next(
        payload for method, path, payload in bridge["requests"] if method == "POST" and path == "/briefs"
    )
    assert set(submitted) == {"concept", "constraints", "preferred_sources", "requested_count"}
    assert app.session_state["run"]["strategy"]["strands"]


def test_lost_submission_response_reuses_the_same_run(bridge, client):
    bridge["lose_submission"] = True
    app = launch(open_app())
    assert app.error
    assert len(client.get("/search-runs").json()) == 1
    app.run()
    button(app, "Continue Search").click().run()
    assert not app.exception
    assert len(client.get("/search-runs").json()) == 1
    submissions = [
        payload for method, path, payload in bridge["requests"] if method == "POST" and path == "/search-runs"
    ]
    assert len(submissions) == 2
    assert submissions[0]["idempotency_key"] == submissions[1]["idempotency_key"]


def test_export_failure_after_save_keeps_the_new_version_for_retry(bridge, client):
    app = launch(open_app())
    run = app.session_state["run"]
    pick(app, run, 0)
    bridge["fail_export"] = True
    button(app, "Export Reference Pack").click().run()
    assert app.error and not app.exception
    assert app.session_state["pack_drafts"][run["id"]]["expected_version"] == 1
    button(app, "Export Reference Pack").click().run()
    assert not app.exception
    assert app.session_state["pack_downloads"][run["id"]]["version"] == 2


@pytest.mark.parametrize("late_conflict", [False, True])
def test_conflicts_never_download_the_wrong_selection(bridge, client, late_conflict):
    app = launch(open_app())
    run = app.session_state["run"]
    pick(app, run, 0)
    if late_conflict:
        bridge["race_export"] = True
    else:
        client.put(
            f"/search-runs/{run['id']}/selection",
            json={
                "ordered_ids": [run["results"][1]["id"]],
                "expected_version": 0,
            },
        )
    button(app, "Export Reference Pack").click().run()
    assert app.error and not app.exception
    assert not app.session_state["pack_downloads"].get(run["id"])
    app.run()
    button(app, "Load Saved Pack").click().run()
    assert not app.exception
    expected = client.get(f"/search-runs/{run['id']}").json()["selection"]
    assert app.session_state["pack_drafts"][run["id"]]["expected_version"] == expected["version"]


def test_saved_run_url_restores_the_exported_selection(bridge):
    app = launch(open_app())
    run = app.session_state["run"]
    pick(app, run, 0)
    button(app, "Export Reference Pack").click().run()
    restored = AppTest.from_file(str(APP), default_timeout=15)
    restored.query_params["run"] = run["id"]
    restored.run()
    assert not restored.exception
    assert widget(restored.checkbox, f"pick_{run['id']}_{run['results'][0]['id']}").value


def test_filters_preserve_hidden_selections_and_annotation_edits(bridge, client):
    app = launch(open_app())
    run = SearchRun.model_validate(app.session_state["run"])
    run.results[1].platform = "YouTube"
    run.results[1].strand = "Execution"
    client.app.state.workflow.repository.put("run", run)
    restored = AppTest.from_file(str(APP), default_timeout=15)
    restored.query_params["run"] = run.id
    restored.run()
    record = restored.session_state["run"]
    first = record["results"][0]
    pick(restored, record, 0)
    edit_key = f"edit_{run.id}_{first['id']}_what_to_borrow"
    edit_borrow(restored, record, first, "Preserve this annotation through filters")
    widget(restored.multiselect, f"source_filter_{run.id}").set_value(["YouTube"]).run()
    assert len(restored.checkbox) == 1
    assert first["id"] in restored.session_state["pack_drafts"][run.id]["ordered_ids"]
    widget(restored.multiselect, f"source_filter_{run.id}").set_value([]).run()
    assert widget(restored.checkbox, f"pick_{run.id}_{first['id']}").value
    details(restored, first)
    assert widget(restored.text_area, edit_key).value == "Preserve this annotation through filters"
    button(restored, "Done").click().run()
    widget(restored.multiselect, f"strand_filter_{run.id}").set_value(["Execution"]).run()
    assert len(restored.checkbox) == 1
    assert first["id"] in restored.session_state["pack_drafts"][run.id]["ordered_ids"]


def test_exclusions_are_saved_with_the_pack_and_can_be_restored(bridge, client):
    app = launch(open_app())
    run = app.session_state["run"]
    first, second = run["results"][:2]
    pick(app, run, 0)
    pick(app, run, 1)
    details(app, second)
    widget(app.button, f"exclude_{second['id']}").click().run()
    assert len(app.checkbox) == 2
    assert second["id"] not in app.session_state["pack_drafts"][run["id"]]["ordered_ids"]
    button(app, "Export Reference Pack").click().run()
    saved = client.get(f"/search-runs/{run['id']}").json()["selection"]
    assert saved["ordered_ids"] == [first["id"]] and saved["rejected_ids"] == [second["id"]]
    button(app, "Restore Excluded References").click().run()
    assert len(app.checkbox) == 3
    assert not widget(app.checkbox, f"pick_{run['id']}_{second['id']}").value


def test_only_available_approved_thumbnails_are_rendered(bridge, client, monkeypatch):
    app = launch(open_app())
    run = SearchRun.model_validate(app.session_state["run"])
    for index, item in enumerate(run.results):
        item.thumbnail = ApprovedThumbnail(
            url=f"https://studio.test/images/{index}.jpg",
            source_url=item.source_url,
            usage_status="approved",
            available=index == 0,
        )
    client.app.state.workflow.repository.put("run", run)
    shown = []
    monkeypatch.setattr("streamlit.image", lambda url, **kwargs: shown.append(url))
    restored = AppTest.from_file(str(APP), default_timeout=15)
    restored.query_params["run"] = run.id
    restored.run()
    assert not restored.exception
    assert shown == [str(run.results[0].thumbnail.url)]


def test_frontend_handles_backend_unavailable(monkeypatch):
    def unavailable(*args, **kwargs):
        raise APIError("Cannot connect to the backend.")

    monkeypatch.setattr(APIClient, "request", unavailable)
    app = open_app()
    assert app.error
    assert button(app, "Retry")


def test_cards_only_render_editors_for_the_open_reference(bridge):
    app = launch(open_app())
    run = app.session_state["run"]
    first, second = run["results"][:2]
    assert not [item for item in app.text_area if (item.key or "").startswith("edit_")]
    assert not any(item.label == "Details / Edit" for item in app.expander)
    details(app, first)
    editors = [item for item in app.text_area if (item.key or "").startswith("edit_")]
    assert len(editors) == 4
    assert all(first["id"] in item.key for item in editors)
    assert any(item.value == "**Rights and usage**" for item in app.markdown)
    button(app, "Done").click().run()
    assert not [item for item in app.text_area if (item.key or "").startswith("edit_")]
    details(app, second)
    assert all(second["id"] in item.key for item in app.text_area if (item.key or "").startswith("edit_"))


def test_borrow_summary_is_short_without_changing_the_annotation():
    from frontend.layout import borrow_summary

    original = "First creative principle. Second creative principle! Extra detail belongs in the dialog."
    assert borrow_summary(original) == "First creative principle. Second creative principle!"
    assert len(borrow_summary("An extended creative idea " * 50)) <= 280


def test_new_search_from_results_preserves_previous_drafts_and_retry_key(bridge, client):
    app = launch(open_app())
    original = app.session_state["run"]
    pick(app, original, 0)
    edit_borrow(app, original, original["results"][0], "Keep the first research draft")
    assert any(item.label == "New Search" for item in app.expander)
    bridge["lose_submission"] = True
    launch(app, concept="Another idea, submitted directly from results")
    assert app.error and len(client.get("/search-runs").json()) == 2
    app.run()
    button(app, "Continue Search").click().run()
    assert not app.exception
    assert app.session_state["run_id"] != original["id"]
    assert len(client.get("/search-runs").json()) == 2
    next(item for item in app.selectbox if item.label == "Recent Research").set_value(original["id"]).run()
    button(app, "Open Research").click().run()
    draft = app.session_state["pack_drafts"][original["id"]]
    assert draft["ordered_ids"] == [original["results"][0]["id"]]
    assert (
        draft["annotations"][original["results"][0]["id"]]["what_to_borrow"]
        == "Keep the first research draft"
    )
