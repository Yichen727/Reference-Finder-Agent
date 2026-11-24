"""Bullet rendering, original-page galleries and cache upgrades without network or AI."""

import base64
from io import BytesIO

import pytest
from PIL import Image

from backend.services.annotation_text import borrow_points
from backend.services.preview_images import PreviewHTML
from backend.services.thumbnails import PREVIEW_VERSION, ThumbnailService
from frontend.layout import preview_images
from tests.conftest import completed_run
from tests.pptx_helpers import NS, slides_from_bytes
from tests.test_article_previews import article
from tests.test_presentation import preview_environment as preview_environment


def png(color):
    output = BytesIO()
    Image.new("RGB", (640, 360), color).save(output, "PNG")
    return output.getvalue()


@pytest.mark.parametrize("value,expected", [
    ("- Use close framing.\n- Connect scenes with one motif.\n- End with a shared action.",
     ["Use close framing.", "Connect scenes with one motif.", "End with a shared action."]),
    ("1. Use close framing.\n   Keep the audience visible.\n2. End with a shared action.",
     ["Use close framing. Keep the audience visible.", "End with a shared action."]),
    ("Borrow the rhythm. Connect the characters. End with a shared action.",
     ["Borrow the rhythm.", "Connect the characters.", "End with a shared action."]),
    ("A. B. C. D. E.", ["A.", "B. C.", "D. E."]),
    ("Keep the existing single sentence", ["Keep the existing single sentence"]),
    ("", []), (None, []),
])
def test_borrow_points_preserve_current_and_historical_advice(value, expected):
    assert borrow_points(value) == expected


def test_pptx_contains_editable_bullets_without_changing_saved_annotation(client):
    run, _ = completed_run(client, count=1)
    item = run["results"][0]
    advice = "- Frame the action around one person.\n- Repeat a visual motif.\n- End on the shared purpose."
    client.put(f"/search-runs/{run['id']}/selection", json={
        "ordered_ids": [item["id"]], "expected_version": 0,
        "annotation_overrides": {item["id"]: {**item["annotation"], "what_to_borrow": advice}},
    }).raise_for_status()
    exported = client.post(f"/search-runs/{run['id']}/exports/pptx", json={"expected_selection_version": 1})
    assert exported.status_code == 201, exported.text
    content = client.get(f"/exports/{exported.json()['id']}/download").content
    slide = slides_from_bytes(content)[2]
    bullets = [p for p in slide.findall(".//a:p", NS) if p.find("a:pPr/a:buChar", NS) is not None]
    assert [p.find("a:r/a:t", NS).text for p in bullets] == borrow_points(advice)
    for p in bullets:
        properties = p.find("a:pPr", NS)
        assert properties.find("a:buChar", NS).get("char") == "•"
        assert int(properties.get("indent")) < 0 < int(properties.get("marL"))
        assert int(p.find("a:r/a:rPr", NS).get("sz")) >= 900
        assert list(properties).index(properties.find("a:spcAft", NS)) < list(properties).index(
            properties.find("a:buChar", NS)
        )
    saved = client.get(f"/search-runs/{run['id']}").json()
    assert saved["selection"]["annotation_overrides"][item["id"]]["what_to_borrow"] == advice
    assert saved["results"][0]["annotation"] == item["annotation"]


def gallery(preview_environment, html=None):
    service, item, state = preview_environment
    article(service, item, state, html or (
        '<meta property="og:image" content="/cover.png"><article>'
        + "".join(f'<img src="/{name}.png">'
                  for name in ("cover", "duplicate", "one", "two", "three", "four"))
        + '</article><aside><img src="/advert.png"></aside>'
    ))
    state["image_bodies"] = {f"/{name}.png": png(color) for name, color in [
        ("cover", "red"), ("duplicate", "red"), ("one", "green"),
        ("two", "blue"), ("three", "orange"), ("four", "purple"),
    ]}
    return service, item, state


def test_gallery_keeps_cover_filters_duplicates_and_is_cached_across_service_instances(preview_environment):
    service, item, state = gallery(preview_environment)
    result = service.resolve(item)
    assert [p["source_url"].rsplit("/", 1)[-1] for p in result["images"]] == [
        "cover.png", "one.png", "two.png", "three.png",
    ]
    assert result["image"] == result["images"][0]["image"]
    assert not any(request.url.path in {"/four.png", "/advert.png"} for request in state["requests"])
    count = len(state["requests"])
    fresh = ThumbnailService(service.settings, service.store, fetcher_factory=service.fetcher_factory)
    assert fresh.resolve(item) == result
    assert len(state["requests"]) == count


def test_legacy_cover_is_enriched_once_and_survives_page_failure(preview_environment):
    service, item, state = gallery(preview_environment)
    old = {"image": "data:image/png;base64," + base64.b64encode(png("red")).decode(),
           "source_url": "https://studio.test/cover.png", "basis": "Saved cover", "preview_version": 5}
    service.store.save_preview(service.cache_key(item), old, 3600)
    state["page_status"] = 403
    result = service.resolve(item)
    assert result["image"] == old["image"]
    assert result["images"][0]["source_url"] == old["source_url"]
    assert result["images"][0]["preview_version"] == PREVIEW_VERSION
    assert result["preview_version"] == PREVIEW_VERSION
    count = len(state["requests"])
    assert service.resolve(item) == result and len(state["requests"]) == count


def test_legacy_website_cover_upgrades_to_full_gallery(preview_environment):
    service, item, state = gallery(preview_environment)
    service.store.save_preview(service.cache_key(item), {
        "image": "data:image/png;base64," + base64.b64encode(png("red")).decode(),
        "source_url": "https://studio.test/cover.png", "basis": "Saved cover", "preview_version": 5,
    }, 3600)
    result = service.resolve(item)
    assert len(result["images"]) == 4
    assert not any(request.url.path == "/cover.png" for request in state["requests"])


def test_corrupt_gallery_image_does_not_discard_good_cover_or_later_images(preview_environment):
    service, item, state = gallery(preview_environment)
    state["image_bodies"]["/one.png"] = b"broken image"
    result = service.resolve(item)
    assert len(result["images"]) == 4
    assert all(not p["source_url"].endswith("/one.png") for p in result["images"])


def test_frontend_gallery_uses_validated_raster_data_and_old_single_image_fallback():
    image = "data:image/png;base64," + base64.b64encode(png("red")).decode()
    assert preview_images({"image": image}) == [{"image": image}]
    assert preview_images({"image": image, "images": [{"image": "https://example.test/private"}]}) == [
        {"image": image},
    ]
    assert preview_images({"images": [{"image": "javascript:alert(1)"}, {"image": image},
                                      {"image": image}]}) == [{"image": image}]


def test_logos_are_excluded_even_when_declared_as_the_page_cover(preview_environment):
    service, item, state = gallery(preview_environment, (
        '<meta property="og:image" content="/studio-logo.png">'
        '<article><img src="/one.png" alt="Social campaign film"></article>'
    ))
    result = service.resolve(item)
    assert result["source_url"].endswith("/one.png")
    assert not any(request.url.path == "/studio-logo.png" for request in state["requests"])
    parser = PreviewHTML("https://studio.test/work/film")
    parser.feed('<article><img src="/social-campaign.jpg" alt="Social campaign">'
                '<img src="/hero.jpg" alt="Studio logo"><div class="advertisement">'
                '<img src="/unrelated.jpg"></div></article>')
    assert parser.body_images() == ["https://studio.test/social-campaign.jpg"]
