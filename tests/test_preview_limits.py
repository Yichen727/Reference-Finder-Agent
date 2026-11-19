"""Large source downloads become compact previews; public metadata needs no login."""

import base64
import random
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.schemas.models import PageCheck
from backend.services.pptx_export import preview_bytes
from backend.services.thumbnails import (
    MAX_IMAGE_BYTES,
    MAX_PREVIEW_BYTES,
    MAX_PREVIEW_DIMENSION,
    PREVIEW_VERSION,
)
from frontend.layout import preview_markup
from tests.test_presentation import preview_environment as preview_environment


def image_bytes(size, *, noise=False):
    image = (
        Image.frombytes("RGB", size, random.Random(7).randbytes(size[0] * size[1] * 3))
        if noise
        else Image.new("RGB", size, "#557766")
    )
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_large_source_is_compacted_and_reused_in_cards_and_pptx(preview_environment):
    service, item, state = preview_environment
    state["image"] = image_bytes((1600, 1000), noise=True)
    assert 2_000_000 < len(state["image"]) < MAX_IMAGE_BYTES
    result = service.resolve(item)
    assert result["image"].startswith("data:image/webp;base64,")
    raw = base64.b64decode(result["image"].split(",", 1)[1])
    assert len(raw) <= MAX_PREVIEW_BYTES
    with Image.open(BytesIO(raw)) as image:
        assert max(image.size) <= MAX_PREVIEW_DIMENSION
        assert image.width / image.height == pytest.approx(1.6)
    assert "<img" in preview_markup(result, item.title)
    calls = len(state["requests"])
    run = SimpleNamespace(results=[item], selection=SimpleNamespace(ordered_ids=[item.id]))
    assert service.for_export(run) == {item.id: result}
    with Image.open(BytesIO(preview_bytes(result))) as image:
        assert image.format == "PNG" and image.size == (1200, 676)
    assert len(state["requests"]) == calls


def test_image_at_exact_download_limit_is_accepted(preview_environment):
    service, item, state = preview_environment
    # PNG readers permit trailing bytes; exercise the exact HTTP payload boundary.
    state["image"] += bytes(MAX_IMAGE_BYTES - len(state["image"]))
    result = service.resolve(item)
    assert result["image"]
    assert len(base64.b64decode(result["image"].split(",", 1)[1])) <= MAX_PREVIEW_BYTES


@pytest.mark.parametrize("boundary", ["content_length", "stream"])
def test_source_over_download_limit_is_rejected(preview_environment, boundary):
    service, item, state = preview_environment
    if boundary == "content_length":
        state["image_headers"] = {"content-length": str(MAX_IMAGE_BYTES + 1)}
    else:
        state["image"] += bytes(MAX_IMAGE_BYTES + 1 - len(state["image"]))
    result = service.resolve(item)
    assert not result["image"] and result["reason"] == "Preview is too large."


def test_small_file_with_large_dimensions_is_resized(preview_environment):
    service, item, state = preview_environment
    state["image"] = image_bytes((2000, 1000))
    assert len(state["image"]) < MAX_PREVIEW_BYTES
    result = service.resolve(item)
    with Image.open(BytesIO(base64.b64decode(result["image"].split(",", 1)[1]))) as image:
        assert image.size == (1280, 640)


def test_resizing_preserves_transparency(preview_environment):
    service, item, state = preview_environment
    output = BytesIO()
    Image.new("RGBA", (2000, 1000), (255, 0, 0, 0)).save(output, format="PNG")
    state["image"] = output.getvalue()
    result = service.resolve(item)
    with Image.open(BytesIO(base64.b64decode(result["image"].split(",", 1)[1]))) as image:
        assert image.size == (1280, 640)
        assert image.convert("RGBA").getpixel((0, 0))[3] == 0


def test_resizing_applies_exif_orientation(preview_environment):
    service, item, state = preview_environment
    output = BytesIO()
    image = Image.new("RGB", (2000, 1000), "#557766")
    exif = Image.Exif()
    exif[274] = 6
    image.save(output, format="JPEG", exif=exif)
    state["image"], state["image_type"] = output.getvalue(), "image/jpeg"
    result = service.resolve(item)
    with Image.open(BytesIO(base64.b64decode(result["image"].split(",", 1)[1]))) as image:
        assert image.size == (640, 1280)
        assert image.getexif().get(274) is None


def test_pixel_limit_still_applies_to_small_compressed_files(preview_environment):
    service, item, state = preview_environment
    state["image"] = image_bytes((4000, 3001))
    assert len(state["image"]) < MAX_IMAGE_BYTES
    result = service.resolve(item)
    assert not result["image"] and "dimensions" in result["reason"]


def test_existing_preview_is_compacted_without_downloading_again(preview_environment):
    service, item, state = preview_environment
    service.store.save_preview(
        service.cache_key(item),
        {
            "image": "data:image/png;base64," + base64.b64encode(image_bytes((2000, 1000))).decode(),
            "source_url": state["image_url"],
            "basis": "Original source attribution",
            "preview_version": 4,
        },
        3600,
    )
    result = service.resolve(item)
    assert result["preview_version"] == PREVIEW_VERSION
    assert result["source_url"] == state["image_url"]
    assert result["basis"] == "Original source attribution"
    assert result["image"].startswith("data:image/webp;base64,")
    assert not state["requests"]
    assert service.resolve(item) == result


@pytest.mark.parametrize("platform", ["Instagram", "Pinterest", "studio.test"])
def test_public_image_metadata_is_used_even_with_a_login_prompt(preview_environment, platform):
    service, item, state = preview_environment
    item.source_url = "https://studio.test/work/futures"
    item.platform = platform
    item.page_check = PageCheck(original_url=str(item.source_url), status="restricted")
    state["html"] = (
        '<meta property="og:image" content="https://cdn.test/public-cover.png">'
        '<div class="login-modal">Sign in to continue</div>'
    )
    result = service.resolve(item)
    assert result["image"] and result["source_url"] == "https://cdn.test/public-cover.png"
    assert all(
        "authorization" not in request.headers and "cookie" not in request.headers
        for request in state["requests"]
    )


@pytest.mark.parametrize("status", [401, 403, 429])
@pytest.mark.parametrize("location", ["page", "image"])
def test_actual_access_denial_is_not_bypassed(preview_environment, status, location):
    service, item, state = preview_environment
    item.source_url = "https://studio.test/work/futures"
    item.platform = "Instagram"
    state["html"] = '<meta property="og:image" content="/public-cover.png">'
    if location == "page":
        state["page_status"] = status
    else:
        state["image_statuses"] = {"/public-cover.png": status}
    result = service.resolve(item)
    assert not result["image"] and str(status) in result["reason"]
    if location == "page":
        assert not any(request.url.path == "/public-cover.png" for request in state["requests"])
