"""Global favorites and current-pack UI, using four offline references per research."""

import copy

import pytest
from streamlit.testing.v1 import AppTest

from frontend.api_client import APIClient, APIError
from tests.pptx_helpers import slide_text, slides_from_bytes
from tests.test_frontend import APP, button, card_count, details, launch, open_app, pick, widget
from tests.test_frontend import bridge as bridge
from tests.test_library import research


@pytest.fixture
def global_bridge(bridge):
    bridge["global_library"] = True
    return bridge


def pack(app):
    return app.session_state["library_state"]["current_pack"]


def favorite(app, run, index=0):
    widget(app.button, f"favorite_{run['id']}_{run['results'][index]['id']}").click().run()
    assert not app.exception


def open_favorites(app):
    widget(app.button, "open_favorites").click().run()
    assert not app.exception


def create_ui_pack(app, name):
    widget(app.button, "pack_manage_create").click().run()
    next(item for item in app.text_input if item.label == "Pack name").input(name)
    widget(app.button, "submit_pack_management").click().run()
    assert not app.exception


def test_bookmark_and_pack_are_independent_and_favorites_reuse_cards(global_bridge):
    app = launch(open_app(), count=4)
    run = app.session_state["run"]
    assert card_count(app) == 4 and len(pack(app)["ordered_ids"]) == 0
    favorite(app, run)
    assert len(app.session_state["library_state"]["favorites"]) == 1
    assert not pack(app)["ordered_ids"]
    pick(app, run, 1)
    assert len(pack(app)["ordered_ids"]) == 1
    open_favorites(app)
    assert card_count(app) == 1
    assert len(pack(app)["ordered_ids"]) == 1
    assert any(item.value == "Favorites" for item in app.subheader)
    favorite_id = app.session_state["library_state"]["favorites"][0]["id"]
    widget(app.button, f"pick_action_favorites_{favorite_id}").click().run()
    assert len(pack(app)["ordered_ids"]) == 2
    current_ids = pack(app)["ordered_ids"]
    widget(app.button, f"favorite_favorites_{favorite_id}").click().run()
    assert not app.exception and card_count(app) == 0
    assert pack(app)["ordered_ids"] == current_ids


def test_current_pack_survives_research_switch_and_page_refresh(global_bridge, client):
    app = launch(open_app(), count=4)
    first = copy.deepcopy(app.session_state["run"])
    pick(app, first, 0)
    first_pack = copy.deepcopy(pack(app))
    second = research(client, "another-research", distinct=True)
    button(app, "New Research").click().run()
    widget(app.button, f"research_{second['id']}").click().run()
    assert pack(app)["id"] != first_pack["id"] and not pack(app)["ordered_ids"]
    second_pack_id = pack(app)["id"]
    widget(app.selectbox, f"current_pack_choice_{second_pack_id}").set_value(first_pack["id"]).run()
    assert pack(app) == first_pack
    pick(app, second, 1)
    assert len(pack(app)["ordered_ids"]) == 2
    widget(app.button, f"research_{first['id']}").click().run()
    assert len(pack(app)["ordered_ids"]) == 2
    assert (
        widget(app.button, f"pick_action_{first['id']}_{first['results'][0]['id']}").proto.type == "primary"
    )
    expected = copy.deepcopy(pack(app))
    restored = AppTest.from_file(str(APP), default_timeout=15)
    restored.query_params["run"] = second["id"]
    restored.run()
    assert not restored.exception and pack(restored) == expected


def test_new_research_adds_and_activates_its_own_pack_without_changing_the_old_pack(global_bridge, client):
    app = launch(open_app(), count=4)
    first_run = copy.deepcopy(app.session_state["run"])
    pick(app, first_run, 0)
    original = copy.deepcopy(pack(app))
    original_count = len(app.session_state["library_state"]["packs"])
    button(app, "New Research").click().run()
    assert len(client.get("/library").json()["packs"]) == original_count
    launch(app, concept="A second creative research", count=4)
    assert not app.exception and pack(app)["id"] != original["id"]
    assert not pack(app)["ordered_ids"]
    assert len(app.session_state["library_state"]["packs"]) == original_count + 1
    assert pack(app)["name"] == app.session_state["run"]["strategy"]["research_title"]
    assert client.get(f"/packs/{original['id']}").json() == original


def test_recent_research_collapse_preserves_active_view_and_pack(global_bridge):
    app = launch(open_app(), count=4)
    run = app.session_state["run"]
    pick(app, run, 0)
    before = copy.deepcopy(pack(app))
    widget(app.button, "toggle_recent_research").click().run()
    assert app.session_state["recent_research_collapsed"]
    assert not any((item.key or "").startswith("research_") for item in app.sidebar.button)
    assert card_count(app) == 4 and pack(app) == before
    open_favorites(app)
    assert app.session_state["recent_research_collapsed"] and pack(app) == before
    widget(app.button, "toggle_recent_research").click().run()
    assert not app.session_state["recent_research_collapsed"]
    widget(app.button, f"research_{run['id']}").click().run()
    assert not app.exception and card_count(app) == 4 and pack(app) == before


def test_create_rename_switch_and_delete_packs_from_favorites(global_bridge):
    app = launch(open_app(), count=4)
    run = app.session_state["run"]
    favorite(app, run)
    original = copy.deepcopy(pack(app))
    open_favorites(app)
    create_ui_pack(app, "Favorites Moodboard")
    new_id = pack(app)["id"]
    assert pack(app)["name"] == "Favorites Moodboard" and new_id != original["id"]
    saved_id = app.session_state["library_state"]["favorites"][0]["id"]
    widget(app.button, f"pick_action_favorites_{saved_id}").click().run()
    widget(app.button, "pack_manage_rename").click().run()
    next(item for item in app.text_input if item.label == "Pack name").input("Renamed Moodboard")
    widget(app.button, "submit_pack_management").click().run()
    assert pack(app)["name"] == "Renamed Moodboard"
    widget(app.selectbox, f"current_pack_choice_{new_id}").set_value(original["id"]).run()
    assert pack(app)["id"] == original["id"] and not pack(app)["ordered_ids"]
    widget(app.selectbox, f"current_pack_choice_{original['id']}").set_value(new_id).run()
    assert pack(app)["ordered_ids"] == [saved_id]
    widget(app.button, "pack_manage_delete").click().run()
    assert pack(app)["id"] == new_id  # No deletion before explicit confirmation.
    widget(app.button, "confirm_pack_delete").click().run()
    assert not app.exception and pack(app)["id"] == original["id"]
    assert len(app.session_state["library_state"]["favorites"]) == 1


def test_favorites_details_and_export_survive_deleting_original_research(global_bridge, client):
    app = launch(open_app(), count=4)
    run = copy.deepcopy(app.session_state["run"])
    favorite(app, run)
    open_favorites(app)
    client.delete(f"/search-runs/{run['id']}").raise_for_status()
    saved_id = app.session_state["library_state"]["favorites"][0]["id"]
    details(app, {"id": saved_id})
    assert any(item.value == run["results"][0]["annotation"]["what_to_borrow"] for item in app.markdown)
    button(app, "Done").click().run()
    widget(app.button, f"pick_action_favorites_{saved_id}").click().run()
    button(app, "Export Reference Pack").click().run()
    assert not app.exception
    assert app.session_state["library_downloads"][pack(app)["id"]]["content"].startswith(b"PK")


def test_drag_reorder_persists_and_export_follows_displayed_order(global_bridge, client, monkeypatch):
    app = launch(open_app(), count=4)
    run = app.session_state["run"]
    for index in (0, 2, 3):
        pick(app, run, index)
    current = pack(app)
    order = current["ordered_ids"][::-1]
    from frontend import library

    fired = []

    def component_action(*, data, key, on_action_change):
        if not fired:
            fired.append(True)
            import streamlit as st

            st.session_state[key] = {"action": {"ordered_ids": order, "revision": current["version"]}}
            on_action_change()

    # Supply a component event while executing the real persistence callback in a script context.
    monkeypatch.setattr(library, "pack_list", component_action)
    app.run()
    assert client.get(f"/packs/{current['id']}").json()["ordered_ids"] == order
    app.run()
    button(app, "Export Reference Pack").click().run()
    assert pack(app)["ordered_ids"] == order
    assert app.session_state["library_downloads"][pack(app)["id"]]["version"] == pack(app)["version"]
    content = app.session_state["library_downloads"][pack(app)["id"]]["content"]
    for slide, reference_id in zip(slides_from_bytes(content)[2:-1], order, strict=True):
        assert pack(app)["references"][reference_id]["reference"]["title"].upper() in slide_text(slide)


def test_pack_conflict_retains_displayed_state_until_explicit_reload(global_bridge, client):
    app = launch(open_app(), count=4)
    run = app.session_state["run"]
    before = copy.deepcopy(pack(app))
    client.patch(
        f"/packs/{before['id']}", json={"name": "Changed Elsewhere", "expected_version": before["version"]}
    )
    pick(app, run, 0)
    assert app.error and pack(app) == before
    assert app.session_state["library_pending"]["payload"]["reference_id"] == run["results"][0]["id"]
    button(app, "Load Latest Pack").click().run()
    assert not app.exception and pack(app)["name"] == "Changed Elsewhere"
    assert "library_pending" not in app.session_state


def test_failed_favorite_response_can_retry_without_duplicate_saves(global_bridge, monkeypatch):
    app = launch(open_app(), count=4)
    run = app.session_state["run"]
    real_request = APIClient.request
    failed = []

    def lose_response(self, method, path, payload=None, **kwargs):
        response = real_request(self, method, path, payload, **kwargs)
        if method == "PUT" and path == "/favorites" and not failed:
            failed.append(True)
            raise APIError("Response lost after saving.", status=504)
        return response

    monkeypatch.setattr(APIClient, "request", lose_response)
    favorite(app, run)
    assert app.error
    button(app, "Retry Change").click().run()
    assert not app.exception and len(app.session_state["library_state"]["favorites"]) == 1
