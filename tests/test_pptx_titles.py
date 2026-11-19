"""Local title truncation in both export workflows, with all OpenAI calls forbidden."""

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.services import pptx_export
from backend.services.pptx_package import NS, Package, xml
from backend.services.pptx_titles import TitleLayout
from tests.pptx_helpers import slides_from_bytes
from tests.test_library import add, current, locator
from tests.test_pptx_export import export, selected_run


@pytest.fixture(autouse=True)
def forbid_openai(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("PPTX export must never call OpenAI, including for long titles")

    monkeypatch.setattr("openai.OpenAI", forbidden)


def layout():
    package = Package(pptx_export.TEMPLATE_DIR / pptx_export.SINGLE_TEMPLATE)
    title = pptx_export.named_shapes(xml(package.parts[package.slide_paths()[0]]))["TITLE"]
    return TitleLayout(pptx_export.shape_size(title)[0])


def exported_titles(content):
    titles = []
    for slide in slides_from_bytes(content)[2:-1]:
        shape = pptx_export.named_shapes(slide)["TITLE"]
        value = "".join(node.text or "" for node in shape.findall(".//a:t", NS))
        size = int(shape.find(".//a:rPr", NS).get("sz")) / 100
        assert 18 <= size <= 54 and layout().fits(value)
        assert shape.find("p:txBody/a:bodyPr", NS).get("wrap") == "none"
        titles.append(value)
    return titles


@pytest.mark.parametrize("global_pack", [False, True])
def test_long_titles_export_locally_and_preserve_original_records_after_restart(
    client, settings, global_pack,
):
    run, selected = selected_run(client, count=4)
    workflow = client.app.state.workflow
    stored = workflow.run(run["id"])
    originals = [
        "NIKE FOOTBALL CAMPAIGN " + "A COMMUNITY STORY OF SUPPORTERS AND CLUB IDENTITY " * 18,
        "CRYSTAL PALACE " + "INTRODUCTION FILM ABOUT SUPPORTERS AND SHARED IDENTITY " * 18,
        selected[2]["title"], selected[3]["title"],
    ]
    by_id = {item.id: item for item in stored.results}
    for item, title in zip(selected, originals, strict=True):
        by_id[item["id"]].title = title
    workflow.repository.put("run", stored)
    before = client.get(f"/search-runs/{run['id']}").json()
    pack = None
    if global_pack:
        client.put("/favorites", json=locator(before, 0)).raise_for_status()
        pack = current(client)
        for item in selected:
            pack = add(client, pack, {"run_id": run["id"], "reference_id": item["id"]})
        favorites_before = client.get("/library").json()["favorites"]
    # Old title-cache rows must not influence the new deterministic display titles.
    with workflow.repository.connection() as conn:
        conn.execute(
            "CREATE TABLE pptx_display_titles (cache_key TEXT PRIMARY KEY, display_title TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO pptx_display_titles VALUES ('old-entry', 'Previously generated title')")

    def perform(instance):
        if global_pack:
            response = instance.post(
                f"/packs/{pack['id']}/exports/pptx", json={"expected_version": pack["version"]},
            )
        else:
            response = export(instance, run)
        assert response.status_code == 201, response.text
        downloaded = instance.get(f"/exports/{response.json()['id']}/download")
        assert downloaded.status_code == 200
        expected = [layout().truncate(title).upper() for title in originals]
        assert exported_titles(downloaded.content) == expected
        assert len(slides_from_bytes(downloaded.content)) == 7
        assert instance.get(f"/search-runs/{run['id']}").json() == before

    perform(client)
    perform(client)
    with TestClient(create_app(settings)) as restarted:
        perform(restarted)
        if global_pack:
            assert restarted.get(f"/packs/{pack['id']}").json() == pack
            assert restarted.get("/library").json()["favorites"] == favorites_before


def test_fitting_titles_keep_the_original_text_without_creating_a_cache(client):
    run, chosen = selected_run(client, count=4)
    response = export(client, run)
    assert response.status_code == 201, response.text
    content = client.get(f"/exports/{response.json()['id']}/download").content
    assert exported_titles(content) == [item["title"].upper() for item in chosen]
    with client.app.state.workflow.repository.connection() as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='pptx_display_titles'").fetchone()


@pytest.mark.parametrize("title", ["W" * 5500, "NIKE FOOTBALL " * 300, "ŠKODA COMMUNITY " * 300])
def test_local_renderer_truncates_at_complete_word_boundaries(client, tmp_path, title):
    run, chosen = selected_run(client, count=4)
    workflow = client.app.state.workflow
    stored = workflow.run(run["id"])
    next(item for item in stored.results if item.id == chosen[0]["id"]).title = title
    target = tmp_path / "standalone.pptx"
    pptx_export.render_pptx(target, workflow.brief(stored.brief_id), stored)
    value = exported_titles(target.read_bytes())[0]
    assert value.endswith("…")
    if " " not in title:
        assert value == "…"
    else:
        assert title.startswith(value[:-1]) and title[len(value[:-1])].isspace()
