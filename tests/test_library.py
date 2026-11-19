"""Persistent global favorites and cross-research packs, without model/network calls."""

import base64
import copy
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import HttpUrl

from backend.main import create_app
from backend.schemas.models import SearchRun
from tests.conftest import completed_run
from tests.pptx_helpers import slide_text, slides_from_bytes


def research(client, key="first", *, distinct=False):
    run, _ = completed_run(client, count=4, key=key)
    if distinct:
        stored = client.app.state.workflow.run(run["id"])
        for item in stored.results:
            item.title = "Another Creative Campaign " + item.title
        client.app.state.workflow.repository.put("run", stored)
        brief = client.app.state.workflow.brief(stored.brief_id)
        brief.concept = "A different original creative brief"
        client.app.state.workflow.repository.put("brief", brief)
        run = stored.model_dump(mode="json")
    return run


def locator(run, index=0):
    return {"run_id": run["id"], "reference_id": run["results"][index]["id"]}


def current(client):
    return client.get("/library").json()["current_pack"]


def create_pack(client, name="A Named Pack", key=None):
    response = client.post("/packs", json={"name": name, "idempotency_key": key or str(uuid4())})
    assert response.status_code == 201, response.text
    return response.json()


def add(client, pack, value):
    response = client.post(
        f"/packs/{pack['id']}/references",
        json={
            **value,
            "expected_version": pack["version"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_favorites_deduplicate_across_research_and_persist_after_restart(client, settings):
    first, second = research(client), research(client, "second")
    saved = client.put("/favorites", json=locator(first)).json()
    duplicate = client.put("/favorites", json=locator(second)).json()
    assert saved["id"] == duplicate["id"]
    assert len(duplicate["snapshots"]) == 2
    assert duplicate["snapshots"][1]["run_id"] == second["id"]
    with TestClient(create_app(settings)) as restarted:
        state = restarted.get("/library").json()
        assert len(state["favorites"]) == 1
        assert state["favorites"][0]["snapshots"][0]["reference"] == first["results"][0]


def test_cross_research_pack_keeps_context_order_and_survives_deleting_research(client, settings):
    first, second = research(client), research(client, "second", distinct=True)
    first_before, second_before = copy.deepcopy(first), copy.deepcopy(second)
    pack = add(client, current(client), locator(first))
    pack = add(client, pack, locator(second, 1))
    order = list(reversed(pack["ordered_ids"]))
    pack = client.put(
        f"/packs/{pack['id']}/order",
        json={
            "ordered_ids": order,
            "expected_version": pack["version"],
        },
    ).json()
    assert [pack["references"][key]["run_id"] for key in order] == [second["id"], first["id"]]
    assert client.get(f"/search-runs/{first['id']}").json() == first_before
    assert client.get(f"/search-runs/{second['id']}").json() == second_before
    for run in (first, second):
        assert client.delete(f"/search-runs/{run['id']}").status_code == 200
    with TestClient(create_app(settings)) as restarted:
        assert current(restarted) == pack
        response = restarted.post(
            f"/packs/{pack['id']}/exports/pptx", json={"expected_version": pack["version"]}
        )
        assert response.status_code == 201, response.text
        content = restarted.get(f"/exports/{response.json()['id']}/download").content
    slides = slides_from_bytes(content)
    assert len(slides) == 5
    for slide, key in zip(slides[2:4], order, strict=True):
        assert pack["references"][key]["reference"]["title"].upper() in slide_text(slide)
    with ZipFile(BytesIO(content)) as archive:
        notes = b"\n".join(
            archive.read(name)
            for name in archive.namelist()
            if name.startswith("ppt/notesSlides/") and name.endswith(".xml")
        )
        assert first["id"].encode() in notes and second["id"].encode() in notes
        assert b"A different original creative brief" in notes


def test_favorite_can_create_pack_after_its_original_research_is_deleted(client):
    run = research(client)
    saved = client.put("/favorites", json=locator(run)).json()
    client.delete(f"/search-runs/{run['id']}").raise_for_status()
    pack = create_pack(client, "From Favorites")
    pack = add(client, pack, {"saved_reference_id": saved["id"]})
    assert pack["references"][saved["id"]]["reference"] == run["results"][0]
    assert client.delete(f"/favorites/{saved['id']}").status_code == 200
    assert not client.get("/library").json()["favorites"]
    assert current(client) == pack


def test_pack_duplicate_add_is_idempotent_without_replacing_the_first_context(client):
    first, second = research(client), research(client, "second")
    initial = current(client)
    pack = add(client, initial, locator(first))
    repeated = add(client, initial, locator(second))
    assert repeated == pack and len(pack["ordered_ids"]) == 1
    saved_id = pack["ordered_ids"][0]
    assert pack["references"][saved_id]["run_id"] == first["id"]


def test_added_reference_retains_its_edited_batch_brief_in_favorites_and_export(client):
    parent = research(client)
    batch = client.post(
        f"/search-runs/{parent['id']}/actions",
        json={
            "action": "add",
            "brief": {"concept": "A specific additional batch direction", "requested_count": 4},
            "idempotency_key": "library-add-context",
        },
    ).json()
    parent = client.get(f"/search-runs/{parent['id']}").json()
    saved = client.put("/favorites", json=locator(parent, 4)).json()
    snapshot = saved["snapshots"][0]
    assert snapshot["run_id"] == parent["id"] and snapshot["batch_run_id"] == batch["id"]
    assert snapshot["brief"]["concept"] == "A specific additional batch direction"
    assert snapshot["brief"]["id"] == batch["brief_id"]
    pack = add(client, current(client), {"saved_reference_id": saved["id"]})
    client.delete(f"/search-runs/{parent['id']}").raise_for_status()
    record = client.post(
        f"/packs/{pack['id']}/exports/pptx", json={"expected_version": pack["version"]}
    ).json()
    with ZipFile(BytesIO(client.get(f"/exports/{record['id']}/download").content)) as archive:
        notes = b"\n".join(
            archive.read(name)
            for name in archive.namelist()
            if name.startswith("ppt/notesSlides/") and name.endswith(".xml")
        )
    assert b"A specific additional batch direction" in notes and batch["id"].encode() in notes


@pytest.mark.parametrize("identity", ["alternate_link", "tracking_parameters"])
def test_declared_work_urls_deduplicate_even_when_publishing_titles_differ(client, identity):
    first, second = research(client), research(client, "another", distinct=True)
    workflow = client.app.state.workflow
    stored = workflow.run(second["id"])
    original_url = first["results"][0]["source_url"]
    if identity == "alternate_link":
        stored.results[0].alternate_urls = [HttpUrl(original_url)]
    else:
        stored.results[0].source_url = HttpUrl(original_url + "?utm_source=publication")
    stored.results[0].creator = "A different credited publisher"
    workflow.repository.put("run", stored)
    one = client.put("/favorites", json=locator(first)).json()
    two = client.put("/favorites", json=locator(second)).json()
    assert one["id"] == two["id"] and len(two["snapshots"]) == 2


def test_multiple_packs_create_rename_switch_and_confirmed_delete_preserve_exports(client, settings):
    run = research(client)
    first = add(client, current(client), locator(run))
    record = client.post(
        f"/packs/{first['id']}/exports/pptx",
        json={
            "expected_version": first["version"],
        },
    ).json()
    original = client.get(f"/exports/{record['id']}/download").content
    second = create_pack(client, "Second Pack")
    renamed = client.patch(
        f"/packs/{second['id']}",
        json={
            "name": "Renamed Pack",
            "expected_version": second["version"],
        },
    ).json()
    assert renamed["name"] == "Renamed Pack"
    client.put(f"/packs/{first['id']}/activate").raise_for_status()
    assert current(client) == first
    client.request(
        "DELETE", f"/packs/{first['id']}", json={"expected_version": first["version"]}
    ).raise_for_status()
    assert current(client)["id"] == second["id"]
    assert client.get(f"/exports/{record['id']}/download").content == original
    with TestClient(create_app(settings)) as restarted:
        assert current(restarted)["name"] == "Renamed Pack"
        assert restarted.get(f"/packs/{first['id']}").status_code == 404


def test_pack_creation_reuses_key_and_rejects_conflicting_names(client):
    key = str(uuid4())
    first = create_pack(client, key=key)
    assert create_pack(client, key=key) == first
    assert client.post("/packs", json={"name": "Another Name", "idempotency_key": key}).status_code == 409


def test_pack_conflicts_and_invalid_order_leave_saved_content_unchanged(client):
    run = research(client)
    old = current(client)
    pack = add(client, old, locator(run))
    assert (
        client.post(
            f"/packs/{pack['id']}/references",
            json={
                **locator(run, 1),
                "expected_version": old["version"],
            },
        ).status_code
        == 409
    )
    assert (
        client.put(
            f"/packs/{pack['id']}/order",
            json={
                "ordered_ids": [str(uuid4())],
                "expected_version": pack["version"],
            },
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/packs/{pack['id']}",
            json={
                "name": "Stale Rename",
                "expected_version": old["version"],
            },
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/packs/{pack['id']}/exports/pptx",
            json={
                "expected_version": old["version"],
            },
        ).status_code
        == 409
    )
    assert current(client) == pack


def test_concurrent_pack_edits_commit_one_version_and_rollback_failed_registration(client):
    run = research(client)
    pack = current(client)

    def attempt(index):
        return client.post(
            f"/packs/{pack['id']}/references",
            json={
                **locator(run, index),
                "expected_version": pack["version"],
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(attempt, range(4)))
    assert statuses.count(200) == 1 and statuses.count(409) == 3
    saved = current(client)
    assert len(saved["ordered_ids"]) == 1 and saved["version"] == pack["version"] + 1
    with client.app.state.workflow.repository.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM saved_references").fetchone()[0] == 1


def test_pack_respects_lower_configured_limit_without_losing_existing_entries(client, settings):
    run = research(client)
    settings.max_references = 2
    pack = add(client, current(client), locator(run))
    pack = add(client, pack, locator(run, 1))
    response = client.post(
        f"/packs/{pack['id']}/references",
        json={
            **locator(run, 2),
            "expected_version": pack["version"],
        },
    )
    assert response.status_code == 422 and current(client) == pack


def test_removal_and_reorder_replay_do_not_increment_versions_twice(client):
    run = research(client)
    pack = add(client, current(client), locator(run))
    pack = add(client, pack, locator(run, 1))
    payload = {"expected_version": pack["version"], "ordered_ids": pack["ordered_ids"][::-1]}
    path = f"/packs/{pack['id']}/order"
    changed = client.put(path, json=payload).json()
    assert client.put(path, json=payload).json() == changed
    remove_path = f"/packs/{pack['id']}/references/{changed['ordered_ids'][0]}"
    payload = {"expected_version": changed["version"]}
    removed = client.request("DELETE", remove_path, json=payload).json()
    assert client.request("DELETE", remove_path, json=payload).json() == removed


def test_library_preview_is_saved_independently_and_export_makes_no_downloads(client, monkeypatch):
    run = research(client)
    item = SearchRun.model_validate(run).results[0]
    output = BytesIO()
    Image.new("RGB", (160, 90), "#557766").save(output, "PNG")
    preview = {
        "image": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode(),
        "source_url": "https://studio.test/cover.png",
        "basis": "Website-declared preview image",
    }
    service = client.app.state.thumbnails
    service.store.save_preview(service.cache_key(item), preview, 3600)
    saved = client.put("/favorites", json=locator(run)).json()
    pack = add(client, current(client), {"saved_reference_id": saved["id"]})
    client.delete(f"/search-runs/{run['id']}").raise_for_status()
    with client.app.state.workflow.repository.connection() as conn:
        conn.execute("DELETE FROM reference_previews")
    monkeypatch.setattr(service, "for_run", lambda *args: pytest.fail("Use permanent library preview"))
    assert client.get("/library/presentation").json()["previews"][saved["id"]] == preview
    exported = client.post(f"/packs/{pack['id']}/exports/pptx", json={"expected_version": pack["version"]})
    assert exported.status_code == 201, exported.text
    content = client.get(f"/exports/{exported.json()['id']}/download").content
    with ZipFile(BytesIO(content)) as archive:
        assert any(name.startswith("ppt/media/referencePreview") for name in archive.namelist())


@pytest.mark.parametrize("same_url", [False, True])
def test_same_work_retains_the_chosen_research_thumbnail_in_each_pack(client, same_url):
    first, second = research(client), research(client, "second")
    if same_url:
        stored = client.app.state.workflow.run(second["id"])
        stored.results[0].source_url = HttpUrl(first["results"][0]["source_url"])
        client.app.state.workflow.repository.put("run", stored)
        second = stored.model_dump(mode="json")
    service = client.app.state.thumbnails
    for run, colour in ((first, "red"), (second, "blue")):
        image = BytesIO()
        Image.new("RGB", (160, 90), colour).save(image, "PNG")
        item = SearchRun.model_validate(run).results[0]
        service.store.save_preview(
            service.cache_key(item),
            {
                "image": "data:image/png;base64," + base64.b64encode(image.getvalue()).decode(),
                "source_url": f"https://studio.test/{colour}.png",
                "basis": "Test preview",
            },
            3600,
        )
        if run is first:
            favorite = client.put("/favorites", json=locator(first)).json()
    pack = add(client, current(client), locator(second))
    assert pack["ordered_ids"] == [favorite["id"]]
    assert (
        client.get("/library/presentation")
        .json()["previews"][favorite["id"]]["source_url"]
        .endswith("red.png")
    )
    record = client.post(
        f"/packs/{pack['id']}/exports/pptx", json={"expected_version": pack["version"]}
    ).json()
    with ZipFile(BytesIO(client.get(f"/exports/{record['id']}/download").content)) as archive:
        name = next(name for name in archive.namelist() if name.startswith("ppt/media/referencePreview"))
        with Image.open(BytesIO(archive.read(name))) as image:
            assert image.getpixel((600, 338)) == (0, 0, 255)


def test_legacy_selection_import_is_once_only_and_preserves_manual_annotation(client, settings):
    run = research(client)
    annotation = {**run["results"][0]["annotation"], "what_to_borrow": "My historical annotation."}
    client.put(
        f"/search-runs/{run['id']}/selection",
        json={
            "expected_version": 0,
            "ordered_ids": [run["results"][0]["id"]],
            "annotation_overrides": {run["results"][0]["id"]: annotation},
        },
    ).raise_for_status()
    before = client.get(f"/search-runs/{run['id']}").json()
    imported = None
    for _ in range(2):
        with TestClient(create_app(settings)) as restarted:
            packs = restarted.get("/library").json()["packs"]
            matching = [restarted.get(f"/packs/{pack['id']}").json() for pack in packs]
            matching = [pack for pack in matching if pack["imported_run_id"] == run["id"]]
            assert len(matching) == 1
            assert imported in {None, matching[0]["id"]}
            imported = matching[0]["id"]
            snapshot = matching[0]["references"][matching[0]["ordered_ids"][0]]
            assert snapshot["reference"]["annotation"] == annotation
            assert restarted.get(f"/search-runs/{run['id']}").json() == before
    client.request("DELETE", f"/packs/{imported}", json={"expected_version": 1}).raise_for_status()
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/packs/{imported}").status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"run_id": str(uuid4()), "reference_id": str(uuid4())},
        {"saved_reference_id": str(uuid4())},
        {"run_id": str(uuid4())},
    ],
)
def test_invalid_reference_locators_cannot_create_fabricated_favorites(client, payload):
    response = client.put("/favorites", json=payload)
    assert response.status_code in {404, 422}
    assert not client.get("/library").json()["favorites"]
