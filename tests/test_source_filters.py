"""Category filtering keeps real publishing platforms and independent Pack data intact."""

import copy

import pytest
from streamlit.testing.v1 import AppTest

from backend.services.source_registry import load_source_registry
from frontend.source_filters import reference_category
from tests.test_frontend import APP, card_count, launch, open_app, pick, widget
from tests.test_frontend import bridge as bridge


@pytest.mark.parametrize(
    "item,expected",
    [
        ({"source_category": "Video & Film", "platform": "vimeo.com"}, "Video Platforms"),
        ({"source_category": "Award Archives", "platform": "Vimeo"}, "Award Archives"),
        ({"platform": "Video & Film"}, "Video Platforms"),
        ({"platform": "Vimeo"}, "Video Platforms"),
        ({"platform": "youtube.com"}, "Video Platforms"),
        ({"source_url": "https://player.vimeo.com/video/123", "platform": "Publisher"}, "Video Platforms"),
        ({"source_url": "https://www.creativereview.co.uk/project", "platform": "Publisher"},
         "Creative Press"),
        ({"platform": "Pinterest"}, "Social Platforms"),
        ({"source_url": "https://evilvimeo.com/project", "platform": "evilvimeo.com"}, None),
        ({"source_url": "https://unknown-studio.test/project", "platform": "unknown-studio.test"}, None),
    ],
)
def test_category_resolution_uses_stored_categories_and_safe_legacy_fallbacks(item, expected):
    definitions = [entry.model_dump() for entry in load_source_registry().metadata()]
    assert reference_category(item, definitions) == expected


def test_video_category_groups_publishers_and_migrates_old_filter_without_changing_pack(bridge, client):
    bridge["global_library"] = True
    app = launch(open_app(), count=4)
    run = client.app.state.workflow.run(app.session_state["run_id"])
    for item, platform, category in zip(
        run.results, ["vimeo.com", "YouTube", "creativereview.co.uk", "Video & Film"],
        ["Video Platforms", "Video Platforms", "Creative Press", None], strict=True,
    ):
        item.platform, item.source_category = platform, category
    client.app.state.workflow.repository.put("run", run)
    before = client.get(f"/search-runs/{run.id}").json()
    restored = AppTest.from_file(str(APP), default_timeout=15)
    restored.query_params["run"] = run.id
    restored.run()
    source = widget(restored.multiselect, f"source_filter_{run.id}")
    assert source.options == ["Video Platforms", "Creative Press"]
    pick(restored, before, 2)
    pack_before = copy.deepcopy(restored.session_state["library_state"]["current_pack"])
    source = widget(restored.multiselect, f"source_filter_{run.id}")
    source.set_value(["Video Platforms"]).run()
    assert not restored.exception and card_count(restored) == 3
    assert restored.session_state["library_state"]["current_pack"] == pack_before
    assert widget(restored.get("popover"), f"result_filters_{run.id}").proto.popover.label == "1"
    # An already-open session may still contain the old domain-based filter value.
    restored.session_state[f"source_filter_{run.id}"] = ["vimeo.com"]
    restored.run()
    assert not restored.exception and card_count(restored) == 3
    assert widget(restored.multiselect, f"source_filter_{run.id}").value == ["Video Platforms"]
    assert restored.session_state["library_state"]["current_pack"] == pack_before
    widget(restored.button, f"clear_filters_{run.id}").click().run()
    assert card_count(restored) == 4
    assert client.get(f"/search-runs/{run.id}").json() == before
