from pathlib import Path

from streamlit.testing.v1 import AppTest

from frontend.api_client import APIClient, APIError

APP = Path(__file__).resolve().parents[1] / "frontend" / "app.py"


def button(app, label):
    return next(item for item in app.button if item.label == label)


def area(app, label):
    return next(item for item in app.text_area if item.label == label)


def test_frontend_mock_flow_and_restore(client, monkeypatch):
    def request(self, method, path, payload=None, *, binary=False):
        response = client.request(method, path, json=payload)
        if response.is_error:
            raise APIError(response.json()["error"]["message"])
        return response.content if binary else response.json()

    monkeypatch.setattr(APIClient, "request", request)
    app = AppTest.from_file(str(APP), default_timeout=15).run()
    assert not app.exception
    area(app, "Creative concept *").input("An optimistic short film about a shared future")
    app.number_input[0].set_value(3)
    button(app, "Build research strategy").click().run()
    assert not app.exception
    button(app, "Generate Mock references").click().run()
    assert not app.exception
    assert len(app.checkbox) == 3
    assert all(not item.value for item in app.checkbox)
    app.checkbox[0].check().run()
    editable = next(item for item in app.text_area if item.label == "What to borrow")
    editable.input("An edited principle saved through the interface").run()
    button(app, "Save selection and annotations").click().run()
    assert not app.exception
    run_id = app.session_state["run_id"]
    run = client.get(f"/search-runs/{run_id}").json()
    assert len(run["selection"]["ordered_ids"]) == 1
    override = next(iter(run["selection"]["annotation_overrides"].values()))
    assert override["what_to_borrow"] == "An edited principle saved through the interface"
    button(app, "Generate DOCX from saved selection").click().run()
    assert not app.exception
    assert app.session_state["download"]["content"].startswith(b"PK")
    # A fresh browser session restores saved state from the URL and SQLite.
    restored = AppTest.from_file(str(APP), default_timeout=15)
    restored.query_params["run"] = run_id
    restored.run()
    assert not restored.exception
    assert restored.checkbox[0].value
    assert area(restored, "What to borrow").value == "An edited principle saved through the interface"


def test_frontend_handles_backend_unavailable(monkeypatch):
    def unavailable(*args, **kwargs):
        raise APIError("Cannot connect to the backend.")

    monkeypatch.setattr(APIClient, "request", unavailable)
    app = AppTest.from_file(str(APP), default_timeout=15).run()
    assert not app.exception
    assert "Cannot connect" in app.error[0].value
    assert button(app, "Retry connection / Reload")
