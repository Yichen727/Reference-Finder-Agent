"""Article-image fallback preserves order, permissions and existing preview export."""

import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.schemas.models import PageCheck
from backend.services.page_verification import CheckStopped
from backend.services.preview_images import PreviewHTML
from tests.test_presentation import preview_environment as preview_environment


def article(service, item, state, html, *, approved=True):
    item.source_url = "https://studio.test/work/futures"
    item.platform = "studio.test"
    item.page_check = None
    state["html"] = html
    if approved:
        service.settings.preview_approved_domains = ["studio.test"]


def license_metadata(image_url, license_url="https://creativecommons.org/licenses/by/4.0/"):
    return (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "ImageObject", "contentUrl": image_url, "license": license_url})
        + "</script>"
    )


def test_first_article_image_becomes_cached_card_and_export_preview(preview_environment):
    service, item, state = preview_environment
    article(
        service,
        item,
        state,
        '<header><img src="/logo.png"></header><main><article>'
        '<img src="/first.png" width="640" height="360"><img src="/second.png">'
        '</article></main><footer><img src="/footer.png"></footer>',
    )
    result = service.resolve(item)
    assert result["image"] and result["source_url"] == "https://studio.test/first.png"
    assert result["basis"].startswith("First suitable article image.")
    paths = [request.url.path for request in state["requests"]]
    assert "/logo.png" not in paths and "/second.png" not in paths and "/footer.png" not in paths
    calls = len(state["requests"])
    assert service.resolve(item) == result
    run = SimpleNamespace(results=[item], selection=SimpleNamespace(ordered_ids=[item.id]))
    assert service.for_export(run) == {item.id: result}
    assert len(state["requests"]) == calls


@pytest.mark.parametrize("kind", ["normal", "lazy", "srcset", "data_srcset"])
def test_normal_lazy_and_responsive_article_images_are_supported(preview_environment, kind):
    service, item, state = preview_environment
    markup = {
        "normal": '<img src="../first.png">',
        "lazy": '<img src="data:image/gif;base64,abc" data-src="../first.png">',
        "srcset": '<img src="/placeholder.png" srcset="/small.png 80w, ../first.png 640w">',
        "data_srcset": '<img data-srcset="../first.png 2x" src="/placeholder.png">',
    }[kind]
    article(service, item, state, "<article>" + markup + "</article>")
    result = service.resolve(item)
    assert result["source_url"] == "https://studio.test/first.png"


@pytest.mark.parametrize("permission", ["unknown", "unrelated", "noncommercial", "licensed", "configured"])
def test_body_images_require_the_same_explicit_permission_as_og_images(preview_environment, permission):
    service, item, state = preview_environment
    html = '<article><img src="/first.png"></article>'
    if permission in {"unrelated", "noncommercial", "licensed"}:
        html += license_metadata(
            "https://studio.test/other.png" if permission == "unrelated" else "https://studio.test/first.png",
            "https://creativecommons.org/licenses/by-nc/4.0/"
            if permission == "noncommercial"
            else "https://creativecommons.org/licenses/by/4.0/",
        )
    article(service, item, state, html, approved=permission == "configured")
    result = service.resolve(item)
    assert bool(result["image"]) == (permission in {"licensed", "configured"})
    if permission not in {"licensed", "configured"}:
        assert not any(request.url.path == "/first.png" for request in state["requests"])


def test_first_image_without_permission_is_skipped_for_first_licensed_image(preview_environment):
    service, item, state = preview_environment
    article(
        service,
        item,
        state,
        '<article><img src="/unlicensed.png"><img src="/licensed.png"></article>'
        + license_metadata("https://studio.test/licensed.png"),
        approved=False,
    )
    result = service.resolve(item)
    assert result["source_url"] == "https://studio.test/licensed.png"
    assert not any(request.url.path == "/unlicensed.png" for request in state["requests"])


def test_og_preview_keeps_priority_and_body_is_not_downloaded(preview_environment):
    service, item, state = preview_environment
    article(
        service,
        item,
        state,
        '<meta property="og:image" content="/cover.png"><article><img src="/body.png"></article>',
    )
    result = service.resolve(item)
    assert result["source_url"] == "https://studio.test/cover.png"
    assert not any(request.url.path == "/body.png" for request in state["requests"])


@pytest.mark.parametrize("first", ["broken_og", "broken_body", "pixel"])
def test_unavailable_or_tiny_first_image_falls_back_to_first_usable_body_image(preview_environment, first):
    service, item, state = preview_environment
    initial = (
        '<meta property="og:image" content="/bad.png">' if first == "broken_og" else '<img src="/bad.png">'
    )
    article(service, item, state, "<article>" + initial + '<img src="/good.png"></article>')
    if first == "pixel":
        output = BytesIO()
        Image.new("RGB", (1, 1)).save(output, format="PNG")
        state["image_bodies"] = {"/bad.png": output.getvalue()}
    else:
        state["image_statuses"] = {"/bad.png": 404}
    result = service.resolve(item)
    assert result["source_url"] == "https://studio.test/good.png"


def test_body_fallback_has_bounded_attempts(preview_environment):
    service, item, state = preview_environment
    article(
        service,
        item,
        state,
        "<article>" + "".join(f'<img src="/{index}.png">' for index in range(5)) + "</article>",
    )
    state["image_statuses"] = {f"/{index}.png": 404 for index in range(5)}
    assert not service.resolve(item)["image"]
    paths = [request.url.path for request in state["requests"]]
    assert "/2.png" in paths and "/3.png" not in paths and "/4.png" not in paths


@pytest.mark.parametrize("failure", ["metadata", "image"])
def test_unavailable_embedded_video_can_use_a_permitted_body_image(preview_environment, monkeypatch, failure):
    service, item, state = preview_environment
    article(service, item, state, '<article><img src="/body.png"></article>')
    item.page_check = PageCheck(
        original_url=str(item.source_url),
        final_url=item.source_url,
        status="accessible",
        work_page=True,
        media_urls=["https://youtube.com/embed/abc123xyz00"],
    )
    if failure == "metadata":

        def denied(*args, **kwargs):
            raise CheckStopped("restricted", "Preview source returned HTTP 403.")

        monkeypatch.setattr("backend.services.thumbnails.provider_cover", denied)
    else:
        state["image_statuses"] = {"/vi/abc123xyz00/hqdefault.jpg": 403}
    result = service.resolve(item)
    assert result["source_url"] == "https://studio.test/body.png"
    assert "First suitable article image" in result["basis"]


@pytest.mark.parametrize(
    "ignored",
    [
        '<header><img src="/ignored.png"></header>',
        '<aside><img src="/ignored.png"></aside>',
        '<div class="related-posts"><img src="/ignored.png"></div>',
        '<div hidden><img src="/ignored.png"></div>',
        '<div style="display:none"><img src="/ignored.png"></div>',
        '<img src="/ignored.png" class="author-avatar">',
        '<img src="/ignored.png" alt="Logo">',
        '<img src="/ignored.png" width="1" height="1">',
        '<img src="http://127.0.0.1/ignored.png">',
        '<img src="data:image/png;base64,ignored">',
        '<img src="/ignored.svg">',
    ],
)
def test_decorative_hidden_and_unsafe_images_are_not_body_candidates(ignored):
    parser = PreviewHTML("https://studio.test/work/futures")
    parser.feed("<article>" + ignored + '<img src="/first.png"></article>')
    assert parser.body_images() == ["https://studio.test/first.png"]


def test_article_content_is_preferred_over_main_and_generic_page_images():
    parser = PreviewHTML("https://studio.test/work/futures")
    parser.feed(
        '<img src="/generic.png"><main><img src="/main.png"><article>'
        '<img src="/first.png"><img src="/second.png"></article></main>'
    )
    assert parser.body_images() == ["https://studio.test/first.png", "https://studio.test/second.png"]
