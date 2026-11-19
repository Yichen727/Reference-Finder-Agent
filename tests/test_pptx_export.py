import base64
import hashlib
import json
import shutil
from io import BytesIO
from zipfile import ZipFile

import pytest
from PIL import Image
from pydantic import HttpUrl

from backend.services import pptx_export
from backend.services.pptx_package import NS, Package, qn, xml
from tests.conftest import completed_run
from tests.pptx_helpers import slide_text, slides_from_bytes


def selected_run(client, count=4):
    run, _ = completed_run(client, count=count)
    chosen = list(reversed(run["results"]))
    response = client.put(
        f"/search-runs/{run['id']}/selection",
        json={
            "ordered_ids": [item["id"] for item in chosen],
            "expected_version": 0,
            "annotation_overrides": {
                chosen[0]["id"]: {**chosen[0]["annotation"], "what_to_borrow": "My saved creative principle."}
            },
        },
    )
    assert response.status_code == 200
    return run, chosen


def export(client, run, version=1):
    return client.post(f"/search-runs/{run['id']}/exports/pptx", json={"expected_selection_version": version})


def test_four_references_produce_seven_editable_slides_in_saved_order(client, settings):
    run, chosen = selected_run(client)
    record = export(client, run).json()
    assert record["format"] == "pptx" and record["selection_version"] == 1
    assert record["template_version"] == "2026-11-17-v1"
    response = client.get(f"/exports/{record['id']}/download")
    assert response.status_code == 200
    assert response.headers["content-type"] == pptx_export.PPTX_MIME
    assert ".pptx" in response.headers["content-disposition"]
    slides = slides_from_bytes(response.content)
    base = Package(pptx_export.TEMPLATE_DIR / pptx_export.BASE_TEMPLATE)
    assert len(slides) == 7
    with ZipFile(BytesIO(response.content)) as archive:
        for original in base.slide_paths():
            assert archive.read(original) == base.parts[original]
        for index, (slide, item) in enumerate(zip(slides[2:-1], chosen, strict=True), 3):
            content = slide_text(slide)
            assert item["title"].upper() in content and item["platform"] in content
            assert all(label in content for label in ["WHAT IT IS", "WHAT TO BORROW", "WHY IT FITS BRIEF"])
            link_run = next(
                run
                for run in slide.findall(".//a:r", NS)
                if run.find("a:t", NS).text == "ORIGINAL LINK HERE."
            )
            assert link_run.find("a:rPr", NS).get("u") == "sng"
            assert link_run.find("a:rPr", NS).get("b") == "1"
            assert link_run.find("a:rPr/a:hlinkClick", NS) is not None
            title_shape = next(
                shape
                for shape in slide.findall(".//p:sp", NS)
                if shape.find("p:nvSpPr/p:cNvPr", NS).get("name") == "TITLE"
            )
            assert title_shape.find(".//a:rPr", NS).get("sz") == "5400"
            assert "CONTEXT HERE" not in content and "IMAGE HERE" not in content
            assert str(index) in content and slide.findall(".//p:sp/p:txBody", NS)
        assert "My saved creative principle." in slide_text(slides[2])
        all_parts = b"\n".join(
            archive.read(name) for name in archive.namelist() if name.endswith((".xml", ".rels"))
        )
        for item in chosen:
            assert str(item["source_url"]).encode() in all_parts
        assert b"D7EfgNKghR8" not in all_parts
        assert b"Rights and usage:" in all_parts and b"Evidence " in all_parts
        assert (
            len(
                [
                    name
                    for name in archive.namelist()
                    if name.startswith("ppt/slideMasters/") and name.endswith(".xml")
                ]
            )
            == 2
        )
    Package(settings.data_dir / "exports" / record["filename"]).validate()
    assert len(response.content) < 1_000_000  # Do not copy the 9MB Single package per reference.
    assert client.get(f"/search-runs/{run['id']}").json()["selection"]["version"] == 1


@pytest.mark.parametrize("count", [1, 20])
def test_reference_count_boundaries_have_exact_page_counts(client, count):
    run, _ = selected_run(client, count=count)
    response = export(client, run)
    assert response.status_code == 201, response.text
    content = client.get(f"/exports/{response.json()['id']}/download").content
    assert len(slides_from_bytes(content)) == count + 3


def test_empty_stale_and_archived_packs_cannot_be_exported(client):
    run, _ = completed_run(client)
    assert export(client, run).status_code == 422
    client.put(
        f"/search-runs/{run['id']}/selection",
        json={"ordered_ids": [run["results"][0]["id"]], "expected_version": 0},
    )
    client.put(
        f"/search-runs/{run['id']}/selection",
        json={"ordered_ids": [run["results"][0]["id"]], "expected_version": 1},
    ).raise_for_status()
    assert export(client, run, version=1).status_code == 409
    current = client.app.state.workflow.run(run["id"])
    current.mode = "mock"
    client.app.state.workflow.repository.put("run", current)
    assert export(client, run).status_code == 409


def test_export_is_offline_and_retains_legacy_downloads(client, monkeypatch):
    run, _ = selected_run(client)

    def forbidden(*args, **kwargs):
        raise AssertionError("Export must not perform research or download new previews")

    monkeypatch.setattr(client.app.state.workflow.provider, "strategy", forbidden)
    monkeypatch.setattr(client.app.state.workflow.provider, "search", forbidden)
    monkeypatch.setattr(client.app.state.thumbnails, "resolve", forbidden)
    assert export(client, run).status_code == 201
    legacy = client.post(f"/search-runs/{run['id']}/exports/docx").json()
    response = client.get(f"/exports/{legacy['id']}/download")
    assert "wordprocessingml" in response.headers["content-type"]
    stored = client.app.state.workflow.repository.get("export", legacy["id"])
    stored.pop("format")
    with client.app.state.workflow.repository.connection() as connection:
        connection.execute(
            "UPDATE records SET payload=? WHERE kind='export' AND id=?", (json.dumps(stored), legacy["id"])
        )
    assert client.get(f"/exports/{legacy['id']}/download").content == response.content


@pytest.mark.parametrize("problem", ["missing", "shape", "relationship", "size"])
def test_bad_templates_preserve_the_saved_pack_and_previous_exports(
    client, settings, monkeypatch, tmp_path, problem
):
    run, _ = selected_run(client)
    previous = export(client, run).json()
    before = client.get(f"/search-runs/{run['id']}").json()["selection"]
    resources = tmp_path / "templates"
    shutil.copytree(pptx_export.TEMPLATE_DIR, resources)
    monkeypatch.setattr(pptx_export, "TEMPLATE_DIR", resources)
    if problem == "missing":
        (resources / pptx_export.SINGLE_TEMPLATE).unlink()
    else:
        path = resources / pptx_export.SINGLE_TEMPLATE
        package = Package(path)
        slide = package.slide_paths()[0]
        if problem == "shape":
            root = xml(package.parts[slide])
            root.xpath("//p:cNvPr[@name='TITLE']", namespaces=NS)[0].set("name", "Unexpected title")
            package.put_xml(slide, root)
        elif problem == "relationship":
            del package.parts["ppt/slideLayouts/slideLayout22.xml"]
        else:
            root = xml(package.parts["ppt/presentation.xml"])
            root.find(qn("p:sldSz")).set("cx", "100000")
            package.put_xml("ppt/presentation.xml", root)
        with ZipFile(path, "w") as archive:
            for name, data in package.parts.items():
                archive.writestr(name, data)
        manifest = json.loads((resources / "manifest.json").read_text())
        manifest["templates"][pptx_export.SINGLE_TEMPLATE] = hashlib.sha256(path.read_bytes()).hexdigest()
        (resources / "manifest.json").write_text(json.dumps(manifest))
    result = export(client, run)
    assert result.status_code in {422, 503}, result.text
    assert result.json()["error"]["code"] in {"export_template_missing", "export_template_invalid"}
    assert client.get(f"/search-runs/{run['id']}").json()["selection"] == before
    assert client.get(f"/exports/{previous['id']}/download").status_code == 200
    assert len(list((settings.data_dir / "exports").glob("*.pptx"))) == 1
    assert not list((settings.data_dir / "exports").glob("*.tmp"))


def test_long_text_is_rejected_without_truncating_or_losing_the_saved_analysis(client):
    run, chosen = selected_run(client)
    item = chosen[0]
    annotation = {**item["annotation"], "what_it_is": "A detailed source description. " * 160}
    client.put(
        f"/search-runs/{run['id']}/selection",
        json={
            "ordered_ids": [item["id"]],
            "annotation_overrides": {item["id"]: annotation},
            "expected_version": 1,
        },
    ).raise_for_status()
    response = export(client, run, version=2)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "export_text_overflow"
    assert (
        client.get(f"/search-runs/{run['id']}").json()["selection"]["annotation_overrides"][item["id"]][
            "what_it_is"
        ]
        == annotation["what_it_is"].strip()
    )


def test_expanded_annotation_word_targets_fit_one_slide_without_truncation(client):
    run, _ = completed_run(client, count=4)
    item = run["results"][0]
    sections = {
        "what_it_is": (
            "An original brand film follows a local community as its members prepare for a shared event. "
            "The story connects individual routines with a collective purpose, presenting the organisation "
            "through recognisable people and places. Its factual premise and campaign context are documented "
            "in the supplied project description. Credits remain source verified."
        ),
        "what_to_borrow": (
            "Borrow the progression from individual activity to collective participation, rather than "
            "copying the original setting. Start with recognisable behaviours that reflect the audience "
            "in your brief, then connect them through a shared visual motif. This could give separate "
            "contributors a clear narrative relationship while keeping the brand present through context. "
            "Treat the motif and scene structure as proposed adaptations: the available evidence supports "
            "the broader storytelling "
            "principle, but does not establish the original camera setup, edit pattern or sound design."
        ),
        "why_it_fits": (
            "This approach fits a community-focused introduction because it expresses identity through "
            "people and everyday activity. It can connect an organisation's wider purpose to experiences "
            "that its audience already recognises, rather than relying on generic claims. For the supplied "
            "brief, the "
            "useful connection is a personable tone and a collective message; practical production "
            "requirements need separate confirmation. Audience response remains unmeasured."
        ),
    }
    assert [len(value.split()) for value in sections.values()] == [50, 80, 60]
    response = client.put(
        f"/search-runs/{run['id']}/selection",
        json={
            "ordered_ids": [item["id"]],
            "expected_version": 0,
            "annotation_overrides": {item["id"]: {**item["annotation"], **sections}},
        },
    )
    assert response.status_code == 200
    record = export(client, run)
    assert record.status_code == 201, record.text
    downloaded = client.get(f"/exports/{record.json()['id']}/download")
    slides = slides_from_bytes(downloaded.content)
    assert len(slides) == 4  # One reference still occupies exactly one slide.
    content = slide_text(slides[2])
    for value in sections.values():
        assert value in content
    stored = client.get(f"/search-runs/{run['id']}").json()["selection"]["annotation_overrides"][item["id"]]
    assert all(stored[field] == value for field, value in sections.items())


def test_export_reuses_card_previews_in_the_matching_slide_image_areas(client, monkeypatch):
    run, chosen = selected_run(client)
    workflow, service = client.app.state.workflow, client.app.state.thumbnails
    current = workflow.run(run["id"])
    by_id = {item.id: item for item in current.results}
    expected = {}
    colours = {chosen[0]["id"]: (255, 0, 105), chosen[2]["id"]: (33, 77, 72)}
    for item_id, colour in colours.items():
        image = BytesIO()
        Image.new("RGB", (160, 90), colour).save(image, "PNG")
        preview = {
            "image": "data:image/png;base64," + base64.b64encode(image.getvalue()).decode(),
            "basis": "Official video preview",
            "source_url": f"https://studio.test/{item_id}.png",
        }
        service.store.save_preview(service.cache_key(by_id[item_id]), preview, 3600)
        expected[item_id] = preview
    assert service.for_export(current) == expected
    monkeypatch.setattr(service, "resolve", lambda *args, **kwargs: pytest.fail("Use cached card images"))
    record = export(client, run).json()
    content = client.get(f"/exports/{record['id']}/download").content
    template = Package(pptx_export.TEMPLATE_DIR / pptx_export.SINGLE_TEMPLATE)
    original_slide = xml(template.parts[template.slide_paths()[0]])
    original_area = pptx_export.named_shapes(original_slide)["Rectangle 6"]
    with ZipFile(BytesIO(content)) as archive:
        assert (
            len([name for name in archive.namelist() if name.startswith("ppt/media/referencePreview")]) == 2
        )
        for slide, item in zip(slides_from_bytes(content)[2:-1], chosen, strict=True):
            area = next(
                shape
                for shape in slide.findall(".//p:sp", NS)
                if shape.find("p:nvSpPr/p:cNvPr", NS).get("name") == "Rectangle 6"
            )
            for component in ["off", "ext"]:
                assert (
                    area.find(f"p:spPr/a:xfrm/a:{component}", NS).attrib
                    == original_area.find(f"p:spPr/a:xfrm/a:{component}", NS).attrib
                )
            blip = area.find("p:spPr/a:blipFill/a:blip", NS)
            if item["id"] not in colours:
                assert blip is None and "Preview unavailable" in slide_text(slide)
                continue
            assert blip is not None and "Preview unavailable" not in slide_text(slide)
            slide_part = next(
                name
                for name in archive.namelist()
                if name.startswith("ppt/slides/")
                and name.endswith(".xml")
                and item["title"].upper() in slide_text(xml(archive.read(name)))
            )
            relationships = xml(archive.read(slide_part.replace("slides/", "slides/_rels/") + ".rels"))
            image_target = next(
                node.get("Target") for node in relationships if node.get("Id") == blip.get(qn("r:embed"))
            )
            image_part = pptx_export.resolve(slide_part, image_target)
            with Image.open(BytesIO(archive.read(image_part))) as image:
                assert image.getpixel((600, 338)) == colours[item["id"]]
        notes = b"\n".join(
            archive.read(name)
            for name in archive.namelist()
            if name.startswith("ppt/notesSlides/") and name.endswith(".xml")
        )
        for preview in expected.values():
            assert preview["source_url"].encode() in notes and preview["basis"].encode() in notes


def test_deleting_research_revokes_pptx_download_but_retains_the_file(client, settings):
    run, _ = selected_run(client)
    record = export(client, run).json()
    client.delete(f"/search-runs/{run['id']}").raise_for_status()
    assert client.get(f"/exports/{record['id']}/download").status_code == 404
    assert (settings.data_dir / "exports" / record["filename"]).is_file()


def test_invalid_cached_images_use_the_placeholder_instead_of_breaking_export(client):
    run, _ = selected_run(client)
    service = client.app.state.thumbnails
    item = client.app.state.workflow.run(run["id"]).results[0]
    service.store.save_preview(
        service.cache_key(item),
        {
            "image": "data:image/png;base64,bm90LWFuLWltYWdl",
            "basis": "Approved test fixture",
            "source_url": "https://studio.test/test.png",
        },
        3600,
    )
    response = export(client, run)
    assert response.status_code == 201, response.text
    content = client.get(f"/exports/{response.json()['id']}/download").content
    assert "Preview unavailable" in "\n".join(slide_text(slide) for slide in slides_from_bytes(content))


def test_long_title_shrinks_to_one_line_without_moving_body_or_image(client):
    run, chosen = selected_run(client)
    current = client.app.state.workflow.run(run["id"])
    title = "A film about community energy and the people connecting it"
    item = next(item for item in current.results if item.id == chosen[0]["id"])
    item.title, item.source_url = title, HttpUrl("https://youtube.com/watch?v=abcdef&feature=share")
    client.app.state.workflow.repository.put("run", current)
    response = export(client, run)
    assert response.status_code == 201, response.text
    content = client.get(f"/exports/{response.json()['id']}/download").content
    slide = slides_from_bytes(content)[2]
    assert title.upper() in slide_text(slide)
    title_shape = next(
        shape
        for shape in slide.findall(".//p:sp", NS)
        if shape.find("p:nvSpPr/p:cNvPr", NS).get("name") == "TITLE"
    )
    assert 1800 <= int(title_shape.find(".//a:rPr", NS).get("sz")) < 5400
    assert title_shape.find("p:txBody/a:bodyPr", NS).get("wrap") == "none"
    template = Package(pptx_export.TEMPLATE_DIR / pptx_export.SINGLE_TEMPLATE)
    original = xml(template.parts[template.slide_paths()[0]])
    for name in ["BODY COPY", "Rectangle 6"]:
        original_shape = next(
            shape
            for shape in original.findall(".//p:sp", NS)
            if shape.find("p:nvSpPr/p:cNvPr", NS).get("name") == name
        )
        exported_shape = next(
            shape
            for shape in slide.findall(".//p:sp", NS)
            if shape.find("p:nvSpPr/p:cNvPr", NS).get("name") == name
        )
        for component in ["off", "ext"]:
            assert (
                original_shape.find(f"p:spPr/a:xfrm/a:{component}", NS).attrib
                == exported_shape.find(f"p:spPr/a:xfrm/a:{component}", NS).attrib
            )
    with ZipFile(BytesIO(content)) as archive:
        links = [
            node.get("Target")
            for name in archive.namelist()
            if name.endswith(".rels")
            for node in xml(archive.read(name))
            if node.get("TargetMode") == "External"
        ]
    assert str(item.source_url) in links


def test_missing_analysis_uses_neutral_text_without_reusing_template_examples(client):
    run, chosen = selected_run(client)
    item = chosen[0]
    client.put(
        f"/search-runs/{run['id']}/selection",
        json={
            "ordered_ids": [item["id"]],
            "expected_version": 1,
            "annotation_overrides": {
                item["id"]: {
                    **item["annotation"],
                    "what_it_is": "",
                    "what_to_borrow": "",
                    "why_it_fits": "",
                }
            },
        },
    ).raise_for_status()
    response = export(client, run, version=2)
    assert response.status_code == 201
    slide = slides_from_bytes(client.get(f"/exports/{response.json()['id']}/download").content)[2]
    content = slide_text(slide)
    assert content.count("Not provided.") == 3
    assert "CONTEXT HERE" not in content and "REFERENCE TITLE" not in content
