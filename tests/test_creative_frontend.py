"""Existing UI uses cached galleries and bullet advice without extra research requests."""

from streamlit.testing.v1 import AppTest

from frontend import preview_component
from tests.conftest import completed_run
from tests.test_frontend import APP, details, widget
from tests.test_frontend import bridge as bridge


def test_details_show_bullet_advice_and_reuse_cached_preview(bridge, client, monkeypatch):
    run, _ = completed_run(client, count=4)
    stored = client.app.state.workflow.run(run["id"])
    advice = "- Frame the action closely.\n- Connect scenes through a motif.\n- End on a shared action."
    stored.results[0].annotation.what_to_borrow = advice
    client.app.state.workflow.repository.put("run", stored)
    preview = {"image": "data:image/png;base64,AAAA", "images": [
        {"image": "data:image/png;base64,AAAA"}, {"image": "data:image/png;base64,BBBB"},
    ]}
    client.app.state.thumbnails.for_run = lambda value: {value.results[0].id: preview}
    rendered = []
    monkeypatch.setattr(preview_component, "_renderer", lambda *args: (
        lambda **kwargs: rendered.append(kwargs)
    ))
    app = AppTest.from_file(str(APP), default_timeout=15)
    app.query_params["run"] = run["id"]
    app.run()
    assert not app.exception
    assert len(rendered[-1]["data"]["images"]) == 2
    assert not rendered[-1]["data"]["detail"]
    before = len(bridge["requests"])
    details(app, run["results"][0])
    assert any(markdown.value == advice for markdown in app.markdown)
    assert any(value["data"]["detail"] for value in rendered)
    assert len(bridge["requests"]) == before
    widget(app.button, f"detail_select_{run['results'][0]['id']}").click().run()
    assert not app.exception
    assert not any(path.endswith("/strategy") or path == "/search-runs"
                   for method, path, _ in bridge["requests"] if method == "POST")
