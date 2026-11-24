import copy
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from streamlit.testing.v1 import AppTest

from backend.schemas.models import ApprovedThumbnail, RankingDetails, SearchRun
from backend.services.source_registry import load_source_registry
from frontend.api_client import APIClient, APIError
from tests.pptx_helpers import slide_text, slides_from_bytes

APP = Path(__file__).resolve().parents[1] / "frontend" / "app.py"


def button(app, label):
    return next(item for item in app.button if item.label == label)


def widget(elements, key):
    return next(item for item in elements if item.key == key)


def area(app, label):
    return next(item for item in app.text_area if item.label == label)


@pytest.fixture
def bridge(client, monkeypatch):
    # Access control has its own tests; these cases exercise the unlocked UI.
    monkeypatch.setattr("frontend.access.require_access", lambda: None)
    # Exercise the historical selection contract; global-library tests enable the new capability.
    state = {"requests": [], "fail_export": False, "lose_submission": False,
             "race_export": False, "global_library": False}

    def request(self, method, path, payload=None, *, binary=False):
        state["requests"].append((method, path, copy.deepcopy(payload)))
        if path.endswith("/presentation") and state.get("fail_presentation"):
            raise APIError("Preview service is temporarily unavailable.", status=503)
        if path == "/source-categories":
            if state.get("fail_sources"):
                raise APIError("Source metadata is unavailable.", status=503)
            if "source_metadata" in state:
                return copy.deepcopy(state["source_metadata"])
        if method in {"DELETE", "PATCH"} and state.get("stale_backend"):
            raise APIError(
                "This HTTP method is not supported for this endpoint.", code="http_error", status=405
            )
        if (method == "DELETE" and state.get("fail_delete")) or (
            method == "PATCH" and state.get("fail_rename")
        ):
            raise APIError("Research management is unavailable.", status=503)
        if method == "POST" and path.endswith("/exports/pptx"):
            if state["fail_export"]:
                state["fail_export"] = False
                raise APIError("Document generation failed.", code="export_failed", status=500)
            if state["race_export"]:
                state["race_export"] = False
                run_url = path.removesuffix("/exports/pptx")
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
        value = response.content if binary else response.json()
        if path == "/health":
            value["capabilities"]["global_library"] = state["global_library"]
        return value

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
    widget(app.button, f"pick_action_{run['id']}_{run['results'][index]['id']}").click().run()


def selected(app, run, index):
    return (
        widget(app.button, f"pick_action_{run['id']}_{run['results'][index]['id']}").proto.type == "primary"
    )


def card_count(app):
    return sum((item.key or "").startswith("pick_action_") for item in app.button)


def details(app, item):
    widget(app.button, f"details_{item['id']}").click().run()
    assert not app.exception


def edit_borrow(app, run, item, value):
    # Simulate an existing unsaved draft from the previous editable interface.
    draft = app.session_state["pack_drafts"][run["id"]]
    draft["annotations"][item["id"]]["what_to_borrow"] = value
    draft["revision"] += 1
    app.run()
    assert not app.exception


def reorder(app, run, ids):
    from frontend.curation import apply_pack_action

    draft = app.session_state["pack_drafts"][run["id"]]
    assert apply_pack_action(draft, {"ordered_ids": ids, "revision": draft["revision"]})
    draft["revision"] += 1
    app.session_state["pack_downloads"].pop(run["id"], None)
    app.run()


def test_home_is_simple_and_defaults_launch_with_one_click(bridge, client):
    app = open_app()
    assert app.title[0].value == "Atomized Reference Finder"
    assert not app.expander
    assert [item.label for item in app.main.button if item.proto.type == "primary"] == ["Find References"]
    area(app, "Describe your creative idea").input("An optimistic character-led campaign")
    button(app, "Find References").click().run()
    assert not app.exception
    run = app.session_state["run"]
    assert len(run["results"]) == 10
    assert run["strategy"]["strands"]
    assert run["mode"] == "live"
    assert sum(method == "POST" and path.endswith("/strategy") for method, path, _ in bridge["requests"]) == 0
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
    reorder(app, run, [second["id"], first["id"]])
    assert app.session_state["pack_drafts"][run["id"]]["ordered_ids"] == [second["id"], first["id"]]
    assert not any((item.key or "").startswith(("up_", "down_")) for item in app.button)
    assert not any("Save selection" in item.label for item in app.button)
    button(app, "Export Reference Pack").click().run()
    assert not app.exception
    saved = client.get(f"/search-runs/{run['id']}").json()["selection"]
    assert saved["ordered_ids"] == [second["id"], first["id"]]
    assert saved["annotation_overrides"][first["id"]]["what_to_borrow"] == "My revised creative principle"
    download = app.session_state["pack_downloads"][run["id"]]
    text = "\n".join(slide_text(slide) for slide in slides_from_bytes(download["content"])[2:-1])
    assert "My revised creative principle" in text
    assert text.index(second["title"].upper()) < text.index(first["title"].upper())
    export = next(
        payload
        for method, path, payload in bridge["requests"]
        if method == "POST" and path.endswith("/exports/pptx")
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
    details(app, second)
    widget(app.button, f"detail_select_{second['id']}").click().run()
    button(app, "Done").click().run()
    assert not selected(app, run, 1)
    button(app, "New Research").click().run()
    launch(app, concept="A different idea")
    widget(app.button, f"research_{run['id']}").click().run()
    assert app.session_state["run_id"] == run["id"]
    assert selected(app, run, 0)
    details(app, first)
    assert any(item.value == "- Keep this draft across navigation" for item in app.markdown)
    assert not app.text_area


def test_search_options_only_expose_sources_and_count_with_a_single_natural_language_brief(bridge, client):
    app = open_app()
    assert not app.expander
    assert [item.label for item in app.text_area] == ["Describe your creative idea"]
    assert [item.label for item in app.multiselect] == ["Sources"]
    assert [item.label for item in app.number_input] == ["Reference Count"]
    assert not app.text_input and not app.checkbox
    prompt = "A playful 30-second film for young commuters. One location, no dialogue, avoid aerial shots."
    launch(app, concept=prompt, count=4)
    assert not app.exception
    brief = app.session_state["brief"]
    assert brief["concept"] == prompt
    submitted = next(
        payload for method, path, payload in bridge["requests"] if method == "POST" and path == "/search-runs"
    )["brief"]
    assert set(submitted) == {"concept", "preferred_sources", "requested_count"}
    assert submitted["concept"] == prompt
    assert app.session_state["run"]["strategy"]["strands"]


@pytest.mark.parametrize("already_in_brief", [False, True])
def test_previous_constraints_draft_merges_into_main_brief_once(bridge, already_in_brief):
    app = open_app()
    constraints = "One location, no dialogue, avoid aerial shots."
    prompt = "A playful film for young commuters."
    if already_in_brief:
        prompt += " " + constraints
    widget(app.multiselect, "brief_sources").set_value([])
    area(app, "Describe your creative idea").input(prompt)
    button(app, "Find References").click().run()  # Save the form without starting a search.
    app.session_state["brief_constraints"] = constraints
    app.run()
    assert not app.exception
    value = area(app, "Describe your creative idea").value
    assert value.startswith(prompt) and value.count(constraints) == 1
    assert "brief_constraints" not in app.session_state
    assert [item.label for item in app.text_area] == ["Describe your creative idea"]
    app.run()
    assert area(app, "Describe your creative idea").value == value


def test_source_categories_come_from_backend_with_all_eight_selected_and_are_cached(bridge):
    app = open_app()
    expected = list(load_source_registry().categories)
    sources = widget(app.multiselect, "brief_sources")
    assert sources.options == sources.value == expected
    assert "Podcasts & Interviews" in sources.options
    assert not app.expander
    app.run()
    assert sum(path == "/source-categories" for _, path, _ in bridge["requests"]) == 1


def test_recent_research_and_results_show_generated_topic_instead_of_prompt_opening(bridge):
    prompt = "Please help me create a film exploring the human side of AI through everyday stories."
    app = launch(open_app(), concept=prompt, count=4)
    run = app.session_state["run"]
    assert run["strategy"]["research_title"] == "Human Side of AI"
    assert widget(app.button, f"research_{run['id']}").label == "Human Side of AI"
    assert any(item.value == "4 Results · Human Side of AI" for item in app.caption)
    assert app.session_state["brief"]["concept"] == prompt
    app.run()
    widget(app.button, f"research_{run['id']}").click().run()
    assert widget(app.button, f"research_{run['id']}").label == "Human Side of AI"
    assert sum(method == "POST" and path.endswith("/strategy") for method, path, _ in bridge["requests"]) == 0


def test_old_preview_session_cache_refreshes_without_losing_pack_draft(bridge):
    app = launch(open_app(), count=4)
    run = app.session_state["run"]
    pick(app, run, 0)
    before = copy.deepcopy(app.session_state["pack_drafts"][run["id"]])
    calls = sum(path.endswith("/presentation") for _, path, _ in bridge["requests"])
    app.session_state["presentation_cache_version"] = 4
    app.run()
    assert not app.exception
    assert app.session_state["presentation_cache_version"] == 6
    assert app.session_state["pack_drafts"][run["id"]] == before
    assert sum(path.endswith("/presentation") for _, path, _ in bridge["requests"]) == calls + 1


def test_preview_endpoint_failure_is_retried_instead_of_cached_as_permanently_empty(bridge):
    bridge["fail_presentation"] = True
    app = launch(open_app(), count=4)
    run = app.session_state["run"]
    assert run["id"] not in app.session_state["presentations"]
    pick(app, run, 0)
    before = copy.deepcopy(app.session_state["pack_drafts"][run["id"]])
    bridge["fail_presentation"] = False
    app.run()
    assert not app.exception
    assert run["id"] in app.session_state["presentations"]
    assert app.session_state["pack_drafts"][run["id"]] == before
    calls = sum(path.endswith("/presentation") for _, path, _ in bridge["requests"])
    app.run()
    assert sum(path.endswith("/presentation") for _, path, _ in bridge["requests"]) == calls


def test_missing_previews_retry_after_cache_expiry_without_changing_selection(bridge):
    app = launch(open_app(), count=4)
    run = app.session_state["run"]
    pick(app, run, 0)
    before = copy.deepcopy(app.session_state["pack_drafts"][run["id"]])
    calls = sum(path.endswith("/presentation") for _, path, _ in bridge["requests"])
    app.session_state["presentation_loaded_at"][run["id"]] = -1000
    app.run()
    assert not app.exception
    assert sum(path.endswith("/presentation") for _, path, _ in bridge["requests"]) == calls + 1
    assert app.session_state["pack_drafts"][run["id"]] == before
    app.run()
    assert sum(path.endswith("/presentation") for _, path, _ in bridge["requests"]) == calls + 1


@pytest.mark.parametrize("choices", [["Video & Film", "Studios & Production Companies"], []])
def test_old_source_cache_refreshes_once_without_losing_draft_preferences(bridge, choices):
    from config import current_source_name

    app = open_app()
    widget(app.multiselect, "brief_sources").set_value([])
    area(app, "Describe your creative idea").input("Keep my unfinished film brief")
    button(app, "Find References").click().run()
    # Simulate an existing Streamlit session when the source labels are upgraded.
    app.session_state["source_names_version"] = 1
    app.session_state["research_source_choices"] = choices
    app.session_state["brief_sources"] = choices
    app.run()
    assert not app.exception
    assert widget(app.multiselect, "brief_sources").options == list(load_source_registry().categories)
    assert widget(app.multiselect, "brief_sources").value == [current_source_name(name) for name in choices]
    assert area(app, "Describe your creative idea").value == "Keep my unfinished film brief"
    assert sum(path == "/source-categories" for _, path, _ in bridge["requests"]) == 2
    app.run()
    assert sum(path == "/source-categories" for _, path, _ in bridge["requests"]) == 2


def test_source_deselections_survive_reruns_submission_and_research_navigation(bridge):
    app = open_app()
    chosen = ["Video Platforms", "Studios & Agencies"]
    widget(app.multiselect, "brief_sources").set_value(chosen)
    button(app, "Find References").click().run()  # Commit the form through validation, without searching.
    app.run()
    assert widget(app.multiselect, "brief_sources").value == chosen
    launch(app, count=4)
    run = app.session_state["run"]
    assert run["strategy"]["preferred_sources"] == chosen
    pick(app, run, 0)
    before = copy.deepcopy(app.session_state["pack_drafts"][run["id"]])
    button(app, "New Research").click().run()
    assert widget(app.multiselect, "brief_sources").value == chosen
    assert "Podcasts & Interviews" not in app.session_state["research_source_choices"]
    widget(app.button, f"research_{run['id']}").click().run()
    assert app.session_state["pack_drafts"][run["id"]] == before
    assert sum(path == "/source-categories" for _, path, _ in bridge["requests"]) == 1


def test_empty_source_selection_stays_empty_and_does_not_submit(bridge):
    app = open_app()
    widget(app.multiselect, "brief_sources").set_value([])
    launch(app, count=4)
    assert any("Select at least one source" in error.value for error in app.error)
    assert widget(app.multiselect, "brief_sources").value == []
    assert not any(method == "POST" for method, _, _ in bridge["requests"])


def test_unavailable_sources_disable_search_and_retry_without_losing_form_input(bridge):
    bridge["fail_sources"] = True
    app = open_app()
    assert app.error and button(app, "Find References").disabled
    assert not app.multiselect
    prompt = "Keep this unfinished creative idea. One location, no dialogue."
    area(app, "Describe your creative idea").input(prompt)
    bridge["fail_sources"] = False
    button(app, "Retry Sources").click().run()
    assert not app.error and not app.exception
    assert area(app, "Describe your creative idea").value == prompt
    assert widget(app.multiselect, "brief_sources").value == list(load_source_registry().categories)
    assert not button(app, "Find References").disabled
    assert not any(method == "POST" for method, _, _ in bridge["requests"])


@pytest.mark.parametrize("response", [None, {}, [], [{"name": "Old source", "default_selected": True}] * 8])
def test_invalid_source_metadata_never_falls_back_or_enables_search(bridge, response):
    bridge["source_metadata"] = response
    app = open_app()
    assert app.error and button(app, "Find References").disabled
    assert not app.multiselect and not app.exception


def test_saved_research_does_not_require_new_search_metadata(bridge):
    app = launch(open_app(), count=4)
    run = app.session_state["run"]
    pick(app, run, 0)
    before = copy.deepcopy(app.session_state["pack_drafts"][run["id"]])
    button(app, "New Research").click().run()
    del app.session_state["source_categories"]
    bridge["fail_sources"] = True
    app.run()
    assert app.error
    widget(app.button, f"research_{run['id']}").click().run()
    assert not app.error and card_count(app) == 4
    assert app.session_state["pack_drafts"][run["id"]] == before


def test_lost_submission_response_reuses_the_same_run(bridge, client):
    bridge["lose_submission"] = True
    app = launch(open_app())
    assert app.error
    assert not app.status and not app.get("progress")
    assert len(client.get("/search-runs").json()) == 1
    requests = len(bridge["requests"])
    app.run()
    assert len(bridge["requests"]) == requests
    button(app, "Continue Search").click().run()
    assert not app.exception
    assert len(client.get("/search-runs").json()) == 1
    submissions = [
        payload for method, path, payload in bridge["requests"] if method == "POST" and path == "/search-runs"
    ]
    assert len(submissions) == 2
    assert submissions[0]["idempotency_key"] == submissions[1]["idempotency_key"]
    assert sum(method == "POST" and path == "/briefs" for method, path, _ in bridge["requests"]) == 0


def test_progress_covers_analysis_source_selection_and_result_preparation(bridge, monkeypatch):
    from frontend import search_progress

    events = []
    original_progress = search_progress.show_search_progress
    original_request = APIClient.request

    def progress(index, message, **kwargs):
        events.append(("progress", index))
        return original_progress(index, message, **kwargs)

    def request(self, method, path, *args, **kwargs):
        events.append((method, path))
        return original_request(self, method, path, *args, **kwargs)

    monkeypatch.setattr(search_progress, "show_search_progress", progress)
    monkeypatch.setattr(APIClient, "request", request)
    app = launch(open_app())
    run = app.session_state["run"]
    assert events.index(("progress", None)) < events.index(("POST", "/search-runs"))
    assert ("POST", "/briefs") not in events
    assert events.index(("progress", 4)) < events.index(("GET", f"/search-runs/{run['id']}/presentation"))
    assert not app.status and not app.get("progress")
    assert card_count(app) == 3


def test_running_progress_uses_pipeline_milestones_and_preserves_submission(bridge, client):
    app = launch(open_app())
    run = SearchRun.model_validate(app.session_state["run"])
    submissions = [item for item in bridge["requests"] if item[0] == "POST"]
    stages = [
        ("queued", "Selecting Sources", 20),
        ("starting_external_search", "Selecting Sources", 20),
        ("searching_1_of_4", "Searching References", 40),
        ("searching_2_of_4", "Searching References", 40),
        ("checking_public_pages", "Verifying & Ranking", 60),
        ("organizing_search_evidence", "Verifying & Ranking", 60),
        ("ranking_and_deduplicating", "Verifying & Ranking", 60),
    ]
    for stage, label, value in stages:
        run.status = "queued" if stage == "queued" else "running"
        run.stage = stage
        client.app.state.workflow.repository.put("run", run)
        app.session_state["run"] = run.model_dump(mode="json")
        app.run()
        assert not app.exception
        assert app.status[0].label == label and app.status[0].state == "running"
        assert app.get("progress")[0].value == value
        assert app.get("progress")[0].text == f"Stage {value // 20 + 1} of 5"
        assert not any(item.label == "Find References" for item in app.button)
        assert not card_count(app)
        if stage == "searching_2_of_4":
            assert any("Searching task 2 of 4" in item.value for item in app.caption)
            assert any("8 source categories selected" in item.value for item in app.caption)
    assert [item for item in bridge["requests"] if item[0] == "POST"] == submissions
    # Polling the same real stage does not advance the bar.
    app.run()
    assert app.get("progress")[0].value == 60
    for status in ["completed", "partial"]:
        run.status = run.stage = status
        client.app.state.workflow.repository.put("run", run)
        app.session_state["run"] = run.model_dump(mode="json")
        app.run()
        assert not app.status and not app.get("progress")
        assert card_count(app) == 3


def test_progress_distinguishes_selected_categories_from_search_tasks(bridge, client):
    app = open_app()
    chosen = ["Video Platforms", "Social Platforms"]
    widget(app.multiselect, "brief_sources").set_value(chosen)
    app = launch(app, count=4)
    run = SearchRun.model_validate(app.session_state["run"])
    run.status, run.stage = "running", "searching_2_of_4"
    client.app.state.workflow.repository.put("run", run)
    app.session_state["run"] = run.model_dump(mode="json")
    app.run()
    assert not app.exception
    assert app.get("progress")[0].text == "Stage 3 of 5"
    assert any(
        "2 source categories selected" in item.value and "Searching task 2 of 4" in item.value
        for item in app.caption
    )


@pytest.mark.parametrize("endpoint", ["/briefs", "/strategy"])
def test_legacy_analysis_failure_clears_progress_and_retries_the_pending_request(
    bridge, client, monkeypatch, endpoint
):
    original_request = APIClient.request
    failure = {"pending": True}

    def request(self, method, path, *args, **kwargs):
        if method == "GET" and path == "/health":
            health = original_request(self, method, path, *args, **kwargs)
            health["capabilities"]["background_planning"] = False
            return health
        if failure["pending"] and method == "POST" and path.endswith(endpoint):
            failure["pending"] = False
            raise APIError("The search service could not complete this request.", status=503)
        return original_request(self, method, path, *args, **kwargs)

    monkeypatch.setattr(APIClient, "request", request)
    app = launch(open_app())
    assert app.error and not app.exception
    assert not app.status and not app.get("progress")
    key = app.session_state["pending_research"]["submission_key"]
    app.run()
    assert not any(item.label == "Find References" for item in app.button)
    button(app, "Continue Search").click().run()
    assert not app.exception
    assert card_count(app) == 3
    assert len(client.get("/search-runs").json()) == 1
    submission = next(
        payload for method, path, payload in bridge["requests"] if path == "/search-runs" and method == "POST"
    )
    assert submission["idempotency_key"] == key


@pytest.mark.parametrize(
    "status,code", [("failed", "no_eligible_references"), ("failed", "run_failed"), ("interrupted", None)]
)
def test_failed_search_replaces_running_progress_with_an_error(bridge, client, status, code):
    app = launch(open_app())
    run = SearchRun.model_validate(app.session_state["run"])
    run.status, run.stage = "running", "checking_public_pages"
    client.app.state.workflow.repository.put("run", run)
    app.session_state["run"] = run.model_dump(mode="json")
    app.run()
    assert app.status and app.get("progress")
    run.status, run.stage, run.error_code = status, status, code
    run.results = []
    client.app.state.workflow.repository.put("run", run)
    app.run()
    assert not app.exception and app.error
    assert not app.status and not app.get("progress")
    assert not card_count(app)


def test_polling_failure_clears_progress_and_recovers_without_resubmission(bridge, client, monkeypatch):
    app = launch(open_app())
    run = SearchRun.model_validate(app.session_state["run"])
    run.status, run.stage = "running", "searching_1_of_4"
    client.app.state.workflow.repository.put("run", run)
    app.session_state["run"] = run.model_dump(mode="json")
    app.run()
    assert app.status
    original_request = APIClient.request

    def request(self, method, path, *args, **kwargs):
        if method == "GET" and path == f"/search-runs/{run.id}":
            raise APIError("Cannot connect to the backend.")
        return original_request(self, method, path, *args, **kwargs)

    monkeypatch.setattr(APIClient, "request", request)
    app.run()
    assert app.error and not app.get("progress") and not app.status
    monkeypatch.setattr(APIClient, "request", original_request)
    app.run()
    assert app.status[0].label == "Searching References"
    assert sum(method == "POST" and path == "/search-runs" for method, path, _ in bridge["requests"]) == 1


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


def test_legacy_download_cache_is_not_mislabelled_as_powerpoint(bridge):
    app = launch(open_app())
    run = app.session_state["run"]
    pick(app, run, 0)
    draft = app.session_state["pack_drafts"][run["id"]]
    app.session_state["pack_downloads"][run["id"]] = {
        "content": b"old-docx",
        "revision": draft["revision"],
        "version": 1,
    }
    app.run()
    assert not app.get("download_button")
    button(app, "Export Reference Pack").click().run()
    assert not app.exception
    assert app.session_state["pack_downloads"][run["id"]]["format"] == "pptx"


def test_old_backend_export_message_keeps_the_saved_pack(bridge, client, monkeypatch):
    original = APIClient.request

    def request(self, method, path, *args, **kwargs):
        if method == "POST" and path.endswith("/exports/pptx"):
            raise APIError("Not found", code="http_error", status=404)
        return original(self, method, path, *args, **kwargs)

    monkeypatch.setattr(APIClient, "request", request)
    app = launch(open_app())
    run = app.session_state["run"]
    pick(app, run, 0)
    button(app, "Export Reference Pack").click().run()
    assert app.error and "Restart the workspace" in app.error[0].value
    assert client.get(f"/search-runs/{run['id']}").json()["selection"]["ordered_ids"] == [
        run["results"][0]["id"]
    ]
    assert selected(app, run, 0)


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
    assert selected(restored, run, 0)


def test_filters_preserve_hidden_selections_and_annotation_edits(bridge, client):
    app = launch(open_app())
    run = SearchRun.model_validate(app.session_state["run"])
    run.results[1].platform = "creativereview.co.uk"
    run.results[1].source_category = "Creative Press"
    run.results[1].strand = "Execution"
    client.app.state.workflow.repository.put("run", run)
    restored = AppTest.from_file(str(APP), default_timeout=15)
    restored.query_params["run"] = run.id
    restored.run()
    record = restored.session_state["run"]
    first = record["results"][0]
    pick(restored, record, 0)
    edit_borrow(restored, record, first, "Preserve this annotation through filters")
    widget(restored.multiselect, f"source_filter_{run.id}").set_value(["Creative Press"]).run()
    assert card_count(restored) == 1
    assert first["id"] in restored.session_state["pack_drafts"][run.id]["ordered_ids"]
    widget(restored.multiselect, f"source_filter_{run.id}").set_value([]).run()
    assert selected(restored, record, 0)
    details(restored, first)
    assert any(item.value == "- Preserve this annotation through filters" for item in restored.markdown)
    button(restored, "Done").click().run()
    restored.session_state[f"strand_filter_{run.id}"] = ["Execution"]
    restored.run()
    assert card_count(restored) == 3
    assert not any(item.label == "Creative Category" for item in restored.multiselect)
    assert first["id"] in restored.session_state["pack_drafts"][run.id]["ordered_ids"]


def test_filter_popover_count_and_clear_preserve_pack_exports_and_exclusions(bridge, client):
    prompt = (
        "A renewable energy brand film exploring local communities, landscapes and a hopeful shared future."
    )
    app = launch(open_app(), concept=prompt)
    run = SearchRun.model_validate(app.session_state["run"])
    title = "Renewable Energy Brand Film"
    client.patch(f"/search-runs/{run.id}/title", json={"title": title})
    run.results[1].platform = "creativereview.co.uk"
    run.results[1].source_category = "Creative Press"
    run.results[1].strand = "Execution"
    client.app.state.workflow.repository.put("run", run)
    app = AppTest.from_file(str(APP), default_timeout=15)
    app.query_params["run"] = run.id
    app.run()
    record = app.session_state["run"]
    assert any(item.value == f"3 Results · {title}" for item in app.caption)
    assert all(prompt not in item.value for item in app.caption)
    assert not any(item.label == "Filter Results" for item in app.expander)
    popover = widget(app.get("popover"), f"result_filters_{run.id}")
    assert [item.label for item in popover.multiselect] == ["Sources"]
    assert popover.proto.popover.label == ""
    assert "tune" in popover.proto.popover.icon
    assert widget(app.button, f"clear_filters_{run.id}").disabled
    pick(app, record, 0)
    details(app, record["results"][2])
    widget(app.button, f"exclude_{record['results'][2]['id']}").click().run()
    button(app, "Export Reference Pack").click().run()
    before = copy.deepcopy(app.session_state["pack_drafts"][run.id])
    download = copy.deepcopy(app.session_state["pack_downloads"][run.id])
    widget(app.multiselect, f"source_filter_{run.id}").set_value(["Creative Press"]).run()
    assert card_count(app) == 1
    assert any(item.value == f"1 Results · {title}" for item in app.caption)
    assert widget(app.get("popover"), f"result_filters_{run.id}").proto.popover.label == "1"
    app.session_state[f"strand_filter_{run.id}"] = ["Story"]
    app.run()
    assert card_count(app) == 1
    assert widget(app.get("popover"), f"result_filters_{run.id}").proto.popover.label == "1"
    assert app.session_state["pack_drafts"][run.id] == before
    widget(app.button, f"clear_filters_{run.id}").click().run()
    assert not app.exception
    assert card_count(app) == 2
    assert selected(app, record, 0)
    assert app.session_state[f"source_filter_{run.id}"] == []
    assert f"strand_filter_{run.id}" not in app.session_state
    assert widget(app.get("popover"), f"result_filters_{run.id}").proto.popover.label == ""
    assert widget(app.button, f"clear_filters_{run.id}").disabled
    assert app.session_state["pack_drafts"][run.id] == before
    assert app.session_state["pack_downloads"][run.id] == download
    requests = len(bridge["requests"])
    app.run()
    assert len(bridge["requests"]) == requests
    assert client.get(f"/briefs/{run.brief_id}").json()["concept"] == prompt


def test_exclusions_are_saved_with_the_pack_and_can_be_restored(bridge, client):
    app = launch(open_app())
    run = app.session_state["run"]
    first, second = run["results"][:2]
    pick(app, run, 0)
    pick(app, run, 1)
    details(app, second)
    widget(app.button, f"exclude_{second['id']}").click().run()
    assert card_count(app) == 2
    assert second["id"] not in app.session_state["pack_drafts"][run["id"]]["ordered_ids"]
    button(app, "Export Reference Pack").click().run()
    saved = client.get(f"/search-runs/{run['id']}").json()["selection"]
    assert saved["ordered_ids"] == [first["id"]] and saved["rejected_ids"] == [second["id"]]
    button(app, "Restore Excluded References").click().run()
    assert card_count(app) == 3
    assert not selected(app, run, 1)


def test_only_backend_validated_preview_bytes_are_rendered(bridge, client):
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
    client.app.state.thumbnails.for_run = lambda run: {
        run.results[0].id: {
            "image": "data:image/png;base64,aGVsbG8=",
            "basis": "Test fixture",
            "source_url": "https://studio.test/image.png",
        },
        run.results[1].id: {"image": "javascript:alert(1)"},
    }
    restored = AppTest.from_file(str(APP), default_timeout=15)
    restored.query_params["run"] = run.id
    restored.run()
    assert not restored.exception
    from frontend.layout import preview_markup

    assert "<img" in preview_markup({"image": "data:image/png;base64,aGVsbG8="}, "Reference")
    assert "<img" not in preview_markup({"image": "javascript:alert(1)"}, "Reference")


def test_frontend_handles_backend_unavailable(monkeypatch):
    monkeypatch.setattr("frontend.access.require_access", lambda: None)

    def unavailable(*args, **kwargs):
        raise APIError("Cannot connect to the backend.")

    monkeypatch.setattr(APIClient, "request", unavailable)
    app = open_app()
    assert app.error
    assert button(app, "Retry")


def test_details_are_read_only_and_open_without_any_api_calls(bridge, client):
    app = launch(open_app(), count=4)
    run = app.session_state["run"]
    first = run["results"][0]
    production = "PRODUCTION_FOR_EXPORT_ONLY"
    assumption = "ASSUMPTION_FOR_EXPORT_ONLY"
    reason = "RANKING_REASON_FOR_BACKEND_ONLY"
    first["annotation"]["production_note"] = production
    first["annotation"]["assumptions"] = [assumption]
    first["ranking"] = RankingDetails(
        score=87,
        components={"relevance": 4},
        reasons=[reason],
        role="execution",
        selection_note="RANKING_SELECTION_FOR_BACKEND_ONLY",
    ).model_dump()
    client.app.state.workflow.repository.put("run", SearchRun.model_validate(run))
    app.session_state["pack_drafts"][run["id"]]["annotations"][first["id"]] = copy.deepcopy(
        first["annotation"]
    )
    before = client.get(f"/search-runs/{run['id']}").json()
    assert not app.text_area
    assert not any(item.label == "New Search" for item in app.expander)
    assert any(item.value == first["annotation"]["what_it_is"] for item in app.main.markdown)
    assert not any("What to Borrow" in item.value or "What It Is" in item.value for item in app.main.markdown)
    calls = list(bridge["requests"])
    details(app, first)
    assert bridge["requests"] == calls
    assert not app.text_area and not app.text_input
    main_sections = ["**What It Is**", "**What to Borrow**", "**Why It Fits Your Brief**"]
    assert [item.value for item in app.markdown if item.value in main_sections] == main_sections
    assert [item.label for item in app.expander] == ["Source & Rights"]
    evidence = next(item for item in app.expander if item.label == "Source & Rights")
    assert not evidence.proto.expanded
    visible = "\n".join(item.value for item in [*app.markdown, *app.caption, *app.text])
    for hidden in [
        "Production Considerations",
        "Recommendation details and assumptions",
        production,
        assumption,
        reason,
        "RANKING_SELECTION_FOR_BACKEND_ONLY",
        "Recommendation fit:",
    ]:
        assert hidden not in visible
    assert first["annotation"]["rights_note"] in visible
    assert first["verification_status"] in visible
    assert run["evidence"][0]["excerpt"] in visible
    assert client.get(f"/search-runs/{run['id']}").json() == before
    widget(app.button, f"detail_select_{first['id']}").click().run()
    assert first["id"] in app.session_state["pack_drafts"][run["id"]]["ordered_ids"]
    assert bridge["requests"] == calls
    button(app, "Done").click().run()
    assert not app.text_area
    button(app, "Export Reference Pack").click().run()
    assert not app.exception
    saved = client.get(f"/search-runs/{run['id']}").json()
    assert saved["results"][0]["ranking"] == first["ranking"]
    override = saved["selection"]["annotation_overrides"][first["id"]]
    assert override["production_note"] == production and override["assumptions"] == [assumption]
    content = app.session_state["pack_downloads"][run["id"]]["content"]
    with ZipFile(BytesIO(content)) as archive:
        notes = "\n".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("ppt/notesSlides/notesSlide") and name.endswith(".xml")
        )
    assert production in notes and assumption in notes


def test_description_summary_is_short_and_handles_missing_legacy_descriptions():
    from frontend.layout import description_summary

    original = (
        "A short film about a coastal town. It follows the local fishing community. Extra source detail."
    )
    assert (
        description_summary(original)
        == "A short film about a coastal town. It follows the local fishing community."
    )
    assert len(description_summary("An extended source description " * 50)) <= 280
    for missing in [None, "", " \n "]:
        assert description_summary(missing) == "Description unavailable. Open the original link for details."


def test_cached_layout_without_description_helper_recovers_without_losing_the_pack(bridge, monkeypatch):
    from frontend import layout

    app = launch(open_app())
    run = app.session_state["run"]
    pick(app, run, 0)
    before = copy.deepcopy(app.session_state["pack_drafts"][run["id"]])
    calls = list(bridge["requests"])
    # Reproduce an existing process holding the module from before the UI update.
    monkeypatch.delattr(layout, "description_summary")
    app.run()
    assert not app.exception
    assert callable(layout.description_summary)
    assert any(item.value == run["results"][0]["annotation"]["what_it_is"] for item in app.main.markdown)
    assert selected(app, run, 0)
    assert app.session_state["pack_drafts"][run["id"]] == before
    assert bridge["requests"] == calls


def test_sidebar_research_navigation_preserves_previous_drafts_and_retry_key(bridge, client):
    app = launch(open_app())
    original = app.session_state["run"]
    pick(app, original, 0)
    edit_borrow(app, original, original["results"][0], "Keep the first research draft")
    assert not any(item.label == "New Search" for item in app.expander)
    button(app, "New Research").click().run()
    bridge["lose_submission"] = True
    launch(app, concept="Another idea, submitted directly from results")
    assert app.error and len(client.get("/search-runs").json()) == 2
    app.run()
    button(app, "Continue Search").click().run()
    assert not app.exception
    assert app.session_state["run_id"] != original["id"]
    assert len(client.get("/search-runs").json()) == 2
    widget(app.button, f"research_{original['id']}").click().run()
    draft = app.session_state["pack_drafts"][original["id"]]
    assert draft["ordered_ids"] == [original["results"][0]["id"]]
    assert (
        draft["annotations"][original["results"][0]["id"]]["what_to_borrow"]
        == "Keep the first research draft"
    )


def assert_header(app):
    assert [item.value for item in app.title] == ["Atomized Reference Finder"]
    assert (
        sum(item.value == "Turn your creative idea into a curated reference pack." for item in app.markdown)
        == 1
    )


def test_header_persists_on_home_active_saved_and_progress_screens(bridge, client):
    app = open_app()
    assert_header(app)
    launch(app)
    assert_header(app)
    run = app.session_state["run"]
    restored = AppTest.from_file(str(APP), default_timeout=15)
    restored.query_params["run"] = run["id"]
    restored.run()
    assert_header(restored)
    record = SearchRun.model_validate(run)
    record.status = "running"
    client.app.state.workflow.repository.put("run", record)
    restored.session_state["run"] = record.model_dump(mode="json")
    restored.run()
    assert_header(restored)
    assert restored.status[0].label == "Finding References"


def test_icon_selection_toggles_without_duplicates_or_annotation_changes(bridge):
    app = launch(open_app())
    run = app.session_state["run"]
    before = copy.deepcopy(app.session_state["pack_drafts"][run["id"]]["annotations"])
    assert not app.checkbox
    pick(app, run, 0)
    assert selected(app, run, 0)
    assert app.session_state["pack_drafts"][run["id"]]["ordered_ids"] == [run["results"][0]["id"]]
    pick(app, run, 0)
    assert not selected(app, run, 0)
    assert app.session_state["pack_drafts"][run["id"]]["ordered_ids"] == []
    pick(app, run, 0)
    assert app.session_state["pack_drafts"][run["id"]]["ordered_ids"] == [run["results"][0]["id"]]
    assert app.session_state["pack_drafts"][run["id"]]["annotations"] == before


def test_sidebar_rename_updates_immediately_and_preserves_current_pack(bridge, client):
    app = launch(open_app())
    run = app.session_state["run"]
    pick(app, run, 0)
    before = copy.deepcopy(app.session_state["pack_drafts"][run["id"]])
    widget(app.button, f"rename_{run['id']}").click().run()
    widget(app.text_input, f"rename_title_{run['id']}").input("A Manually Named Research").run()
    button(app, "Save Title").click().run()
    assert not app.exception
    assert widget(app.button, f"research_{run['id']}").label == "A Manually Named Research"
    assert any(item.value == "3 Results · A Manually Named Research" for item in app.caption)
    assert client.get("/search-runs").json()[0]["title"] == "A Manually Named Research"
    assert app.session_state["pack_drafts"][run["id"]] == before
    assert app.session_state["run_id"] == run["id"]


def test_deleting_active_research_requires_confirmation_and_returns_home(bridge, client):
    app = launch(open_app())
    run = app.session_state["run"]
    pick(app, run, 0)
    button(app, "Export Reference Pack").click().run()
    widget(app.button, f"delete_{run['id']}").click().run()
    assert client.get(f"/search-runs/{run['id']}").status_code == 200
    assert not any(method == "DELETE" for method, _, _ in bridge["requests"])
    button(app, "Cancel").click().run()
    assert selected(app, run, 0)
    widget(app.button, f"delete_{run['id']}").click().run()
    widget(app.button, "confirm_research_delete").click().run()
    assert not app.exception
    assert client.get(f"/search-runs/{run['id']}").status_code == 404
    assert "run_id" not in app.session_state
    assert not app.query_params
    assert run["id"] not in app.session_state["pack_drafts"]
    assert run["id"] not in app.session_state["pack_downloads"]
    assert not any(item.key == f"research_{run['id']}" for item in app.button)
    assert area(app, "Describe your creative idea")
    assert_header(app)


def test_deleting_another_research_preserves_active_draft(bridge, client):
    app = launch(open_app())
    old = app.session_state["run"]
    button(app, "New Research").click().run()
    launch(app, concept="Another creative idea")
    active = app.session_state["run"]
    pick(app, active, 1)
    before = copy.deepcopy(app.session_state["pack_drafts"][active["id"]])
    widget(app.button, f"delete_{old['id']}").click().run()
    widget(app.button, "confirm_research_delete").click().run()
    assert not app.exception
    assert app.session_state["run_id"] == active["id"]
    assert app.session_state["pack_drafts"][active["id"]] == before
    assert client.get(f"/search-runs/{old['id']}").status_code == 404


@pytest.mark.parametrize("operation", ["rename", "delete"])
def test_management_failure_preserves_current_research_and_unsaved_pack(bridge, client, operation):
    app = launch(open_app())
    run = app.session_state["run"]
    pick(app, run, 0)
    before = copy.deepcopy(app.session_state["pack_drafts"][run["id"]])
    bridge[f"fail_{operation}"] = True
    widget(app.button, f"{operation}_{run['id']}").click().run()
    if operation == "rename":
        widget(app.text_input, f"rename_title_{run['id']}").input("Keep the current research")
        button(app, "Save Title").click().run()
    else:
        widget(app.button, "confirm_research_delete").click().run()
    assert not app.exception and app.error
    assert app.session_state["run_id"] == run["id"]
    assert app.session_state["pack_drafts"][run["id"]] == before
    assert client.get(f"/search-runs/{run['id']}").status_code == 200
    button(app, "Cancel").click().run()
    assert not app.exception and selected(app, run, 0)


def test_old_backend_explains_restart_and_preserves_failed_delete_draft(bridge, client):
    app = launch(open_app())
    run = app.session_state["run"]
    pick(app, run, 0)
    before = copy.deepcopy(app.session_state["pack_drafts"][run["id"]])
    bridge["stale_backend"] = True
    widget(app.button, f"delete_{run['id']}").click().run()
    widget(app.button, "confirm_research_delete").click().run()
    assert not app.exception
    assert any("latest backend" in item.value and "Restart the workspace" in item.value for item in app.error)
    assert app.session_state["pack_drafts"][run["id"]] == before
    assert client.get(f"/search-runs/{run['id']}").status_code == 200
    button(app, "Cancel").click().run()
    assert not app.exception and selected(app, run, 0)


def test_workspace_styles_refresh_on_rerun_even_when_python_modules_are_cached(bridge, monkeypatch):
    import streamlit as st

    style_path = APP.with_name("workspace.css")
    original_read = Path.read_text
    stylesheet = {"text": original_read(style_path) + "\n/* workspace-first-revision */"}
    captured = []
    original_html = st.html

    def read_text(path, *args, **kwargs):
        if path == style_path:
            return stylesheet["text"]
        return original_read(path, *args, **kwargs)

    def html(body, *args, **kwargs):
        captured.append(body)
        return original_html(body, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(st, "html", html)
    app = open_app()
    assert "workspace-first-revision" in captured[-1]
    stylesheet["text"] = stylesheet["text"].replace("workspace-first-revision", "workspace-second-revision")
    app.run()
    assert not app.exception
    assert "workspace-second-revision" in captured[-1]
    assert "workspace-first-revision" not in captured[-1]
