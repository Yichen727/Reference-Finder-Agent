"""Universal, offline regressions for the observed page structures and CDN responses."""

import copy
import gzip
import json
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
from PIL import Image

from backend.schemas.models import ApprovedThumbnail
from backend.services.preview_fetching import PreviewFetcher
from backend.services.preview_images import PreviewHTML
from backend.services.thumbnails import PREVIEW_VERSION, ThumbnailService
from tests.test_library import add, current, locator, research
from tests.test_page_verification import public_dns, response


def raster(color, kind="PNG", size=(640, 360)):
    output = BytesIO()
    Image.new("RGB", size, color).save(output, kind)
    return output.getvalue()


class Store:
    def __init__(self):
        self.values = {}

    def preview(self, key):
        return copy.deepcopy(self.values.get(key))

    def save_preview(self, key, value, ttl):
        self.values[key] = copy.deepcopy(value)


@pytest.fixture
def extraction(settings):
    state = {"html": "", "requests": [], "robots": {}, "assets": {}, "page_status": 200}

    def handle(request):
        host, path = request.headers["host"], request.url.path
        state["requests"].append((host, path))
        if path == "/robots.txt":
            status, mime, body = state["robots"].get(host, (200, "text/plain", "User-agent: *\nAllow: /"))
            return response(status=status, body=body.encode(), headers={"content-type": mime})
        if (host, path) in state["assets"]:
            status, mime, body = state["assets"][(host, path)]
            return response(status=status, body=body, headers={"content-type": mime})
        if path.endswith(".png") or path.endswith(".jpg") or path.endswith(".webp"):
            return response(status=404)
        if state.get("gzip"):
            return response(body=gzip.compress(state["html"].encode()), headers={
                "content-type": "text/html", "content-encoding": "gzip",
            })
        return response(status=state["page_status"], body=state["html"].encode())

    transport = httpx.MockTransport(handle)

    def factory(config):
        return PreviewFetcher(config, resolver=public_dns, transport=transport)

    service = ThumbnailService(settings, Store(), fetcher_factory=factory)

    def run(url="https://studio.test/work/current", title="Current Project"):
        item = SimpleNamespace(id="reference", source_url=url, title=title,
                               platform=urlsplit(url).hostname, thumbnail=state.get("thumbnail"),
                               page_check=None)
        return service.resolve(item)

    return service, state, run


def test_dandad_style_page_never_uses_generic_social_or_award_icons(extraction):
    _, state, run = extraction
    state["html"] = (
        '<meta property="og:image" content="/images/social.jpg"><main><h1>With ChatGPT</h1>'
        '<video><source src="https://video.test/film/hls.m3u8"></video>'
        '<img class="vds-poster" src="data:image/png;base64,AAAA">'
        '<a href="/work/another"><img src="/images/yellow-pencil.webp"></a>'
        '<img src="/images/graphite-pencil.webp"></main>'
    )
    result = run("https://www.dandad.org/work/d-ad-awards-archive/with-chatgpt", "With ChatGPT")
    assert not result["image"] and result["images"] == []
    assert not any(path.endswith(("social.jpg", "pencil.webp", "hls.m3u8"))
                   for _, path in state["requests"])
    assert any(entry.get("candidates") == 0 for entry in result["diagnostics"])


def test_drum_style_uses_article_image_not_promotional_video_and_accepts_malformed_mime(extraction):
    _, state, run = extraction
    state["html"] = (
        '<div class="td-above-nav-container__creative"><video poster="https://cdn.test/promo.webp">'
        '</video><img src="https://cdn.test/promotional.png"></div>'
        '<main><article><h1>Champion Campaign</h1><figure><img alt="Champion campaign" '
        'src="https://cdn.test/champion.jpg"></figure><div class="article-byline">'
        '<a href="/author/reporter"><img src="https://cdn.test/reporter.jpg"></a>'
        '</div></article></main>'
    )
    state["robots"]["cdn.test"] = (200, "text/plain, charset=utf-8", "User-agent: *\nDisallow:")
    state["assets"][("cdn.test", "/champion.jpg")] = (200, "image/jpeg", raster("red", "JPEG"))
    result = run("https://www.thedrum.com/news/champion-campaign", "Champion Campaign")
    assert result["source_url"] == "https://cdn.test/champion.jpg"
    assert not any("promo" in path for _, path in state["requests"])
    assert not any("reporter" in path for _, path in state["requests"])
    assert result["source_page_url"].startswith("https://www.thedrum.com/")


def test_clios_style_body_class_does_not_block_gallery_and_unknown_cdn_policy_is_not_image_denial(extraction):
    service, state, run = extraction
    state["html"] = (
        '<body class="wp-singular single wp-custom-logo"><main><h1>Current Project</h1>'
        '<div class="mediaSwiper"><div class="swiper-slide"><img alt="Execution Image" '
        'src="https://cdn.test/one.webp"></div><div class="swiper-slide" aria-hidden="true">'
        '<img alt="Case Study Video" src="https://cdn.test/two.jpg"></div></div>'
        '<section class="related-winners"><img src="https://cdn.test/other.png"></section></main></body>'
    )
    state["robots"]["cdn.test"] = (403, "application/xml", "<Error>AccessDenied</Error>")
    state["assets"][("cdn.test", "/one.webp")] = (200, "binary/octet-stream", raster("blue", "WEBP"))
    state["assets"][("cdn.test", "/two.jpg")] = (200, "image/png", raster("red", "JPEG"))
    result = run("https://clios.com/winners-gallery/details/201854")
    assert len(result["images"]) == 2
    assert result["images"][0]["image"].startswith("data:image/webp;base64,")
    assert result["images"][1]["image"].startswith("data:image/jpeg;base64,")
    assert not any(path == "/other.png" for _, path in state["requests"])
    assert any(entry.get("code") == "unknown_policy" for entry in result["diagnostics"])
    before = len(state["requests"])
    assert run("https://clios.com/winners-gallery/details/201854") == result
    assert len(state["requests"]) == before
    fresh = ThumbnailService(service.settings, service.store, fetcher_factory=service.fetcher_factory)
    item = SimpleNamespace(source_url="https://clios.com/winners-gallery/details/201854", thumbnail=None)
    assert fresh.resolve(item) == result


def test_posters_picture_lazy_and_structured_metadata_are_ranked_as_project_media():
    parser = PreviewHTML("https://studio.test/work/current", title="Current Project")
    parser.feed('<body class="custom-logo"><main><article><header><picture>'
                '<source srcset="/small.webp 100w, /large.webp 1200w">'
                '<img src="data:image/gif;base64,AAAA" alt="Current Project"></picture></header>'
                '<img data-lazy-src="/lazy.jpg"><video poster="/poster.jpg"></video>'
                '</article><div class="project-gallery"><img src="/gallery.png"></div></main>'
                '<script type="application/ld+json">' + json.dumps({
                    "@graph": [{"@type": "Organization", "logo": "/opaque-brand.png"},
                               {"@type": "VideoObject", "name": "Current Project",
                                "thumbnailUrl": "/thumb.jpg"}],
                }) + '</script></body>')
    parser.extract()
    candidates = parser.candidates()
    urls = [candidate.url for candidate in candidates]
    assert set(url.rsplit("/", 1)[-1] for url in urls) == {
        "large.webp", "lazy.jpg", "poster.jpg", "gallery.png", "thumb.jpg",
    }
    assert candidates[0].kind in {"poster", "structured", "body"}


@pytest.mark.parametrize("status", [401, 403, 429])
def test_actual_resource_denial_is_not_bypassed(extraction, status):
    _, state, run = extraction
    state["html"] = '<article><img src="https://cdn.test/frame.jpg"></article>'
    state["assets"][("cdn.test", "/frame.jpg")] = (status, "image/jpeg", raster("red", "JPEG"))
    result = run()
    assert result["image"] is None
    assert f"HTTP {status}" in result["reason"]


@pytest.mark.parametrize("status", [401, 403, 429])
def test_unknown_source_page_policy_does_not_allow_page_probe(extraction, status):
    _, state, run = extraction
    state["robots"]["studio.test"] = (status, "text/plain", "")
    assert run()["image"] is None
    assert not any(path == "/work/current" for _, path in state["requests"])


def test_explicit_image_disallow_wins_even_when_target_would_be_public(extraction):
    _, state, run = extraction
    state["html"] = '<article><img src="https://cdn.test/private.jpg"></article>'
    state["robots"]["cdn.test"] = (200, "text/plain", "User-agent: *\nDisallow: /")
    state["assets"][("cdn.test", "/private.jpg")] = (200, "image/jpeg", raster("red", "JPEG"))
    result = run()
    assert result["image"] is None and "disallows" in result["reason"]
    assert ("cdn.test", "/private.jpg") not in state["requests"]


@pytest.mark.parametrize("mime", ["binary/octet-stream", "application/octet-stream",
                                  "image/jpeg", "text/plain"])
def test_pillow_validates_real_image_format_instead_of_mime_label(extraction, mime):
    _, state, run = extraction
    state["html"] = '<article><img src="/frame.png"></article>'
    state["assets"][("studio.test", "/frame.png")] = (200, mime, raster("green", "WEBP"))
    result = run()
    assert result["image"].startswith("data:image/webp;base64,")
    assert result["declared_mime"] == mime


def test_invalid_bytes_cannot_be_rendered_even_with_image_mime(extraction):
    _, state, run = extraction
    state["html"] = '<article><img src="/frame.png"></article>'
    state["assets"][("studio.test", "/frame.png")] = (200, "image/png", b"<html>Sign in</html>")
    assert run()["image"] is None


def test_duplicate_images_and_shared_robots_cache(extraction):
    _, state, run = extraction
    state["html"] = '<main><article><img src="/one.png"><img src="/two.png"></article></main>'
    for path in ("/one.png", "/two.png"):
        state["assets"][("studio.test", path)] = (200, "image/png", raster("red"))
    assert len(run()["images"]) == 1
    run("https://studio.test/work/second")
    assert state["requests"].count(("studio.test", "/robots.txt")) == 1


def test_generic_cached_share_image_is_invalidated_and_gzip_html_is_supported(extraction):
    service, state, run = extraction
    item = SimpleNamespace(source_url="https://studio.test/work/current", thumbnail=None)
    service.store.save_preview(service.cache_key(item), {
        "image": "data:image/png;base64,AAAA", "source_url": "https://studio.test/images/social.jpg",
        "preview_version": 6,
    }, 3600)
    state["gzip"] = True
    state["html"] = '<article><img src="/frame.png"></article>'
    state["assets"][("studio.test", "/frame.png")] = (200, "image/png", raster("blue"))
    result = run()
    assert result["preview_version"] == PREVIEW_VERSION
    assert result["source_url"] == "https://studio.test/frame.png"


def test_decompressed_html_size_is_bounded(extraction):
    _, state, run = extraction
    state["gzip"] = True
    state["html"] = "<html>" + "a" * 2_000_000 + "</html>"
    assert run()["reason"] == "Preview is too large."


def test_cdn_variants_are_deduplicated_and_gallery_beats_unscoped_metadata():
    parser = PreviewHTML("https://studio.test/work/current", title="Current Project")
    parser.feed('<meta property="og:image" content="https://cdn.test/frame.jpg?w=1280&amp;auto=format">'
                '<main><div class="project-gallery"><img src="https://cdn.test/frame.jpg?w=960">'
                '<img src="https://cdn.test/second.jpg"></div></main>')
    parser.extract()
    candidates = parser.candidates()
    assert len(candidates) == 2
    assert all(candidate.kind == "body" for candidate in candidates)


def test_unrelated_video_poster_is_excluded_but_current_project_poster_survives():
    parser = PreviewHTML("https://studio.test/work/current")
    parser.feed('<main><video data-player-config=\'{"poster":"/frame.jpg"}\'></video>'
                '<section class="related-posts"><video poster="/another-project.jpg"></video>'
                '</section></main>')
    parser.extract()
    assert [candidate.url for candidate in parser.candidates()] == ["https://studio.test/frame.jpg"]


def test_tiny_remote_placeholder_is_not_an_acceptable_preview(extraction):
    _, state, run = extraction
    state["html"] = '<meta property="og:image" content="/empty.png">'
    state["assets"][("studio.test", "/empty.png")] = (200, "image/png", raster("white", size=(1, 1)))
    assert run()["image"] is None


def test_robots_plaintext_with_binary_mime_still_enforces_explicit_disallow(extraction):
    _, state, run = extraction
    state["html"] = '<article><img src="https://cdn.test/frame.jpg"></article>'
    state["robots"]["cdn.test"] = (200, "application/octet-stream", "User-agent: *\nDisallow: /")
    assert run()["image"] is None
    assert ("cdn.test", "/frame.jpg") not in state["requests"]


def test_permanent_old_logo_cache_is_replaced_without_changing_research_or_pack(client):
    import base64

    from backend.schemas.models import SearchRun

    run = research(client)
    service = client.app.state.thumbnails
    item = SearchRun.model_validate(run).results[0]
    old = {"image": "data:image/png;base64," + base64.b64encode(raster("red")).decode(),
           "source_url": "https://studio.test/images/social.jpg", "preview_version": 6,
           "basis": "Old generic share image"}
    service.store.save_preview(service.cache_key(item), old, 3600)
    favorite = client.put("/favorites", json=locator(run)).json()
    pack = add(client, current(client), {"saved_reference_id": favorite["id"]})
    assert service.for_export(SimpleNamespace(results=[item], selection=SimpleNamespace(
        ordered_ids=[item.id]))) == {}
    fresh = {**old, "source_url": "https://studio.test/work/current-frame.jpg", "preview_version": 7}
    service.for_run = lambda value: {value.results[0].id: fresh}
    assert client.get("/library/presentation").json()["previews"][favorite["id"]] == fresh
    assert client.get(f"/search-runs/{run['id']}").json() == run
    assert current(client) == pack


def test_cache_upgrade_retains_valid_gallery_when_source_is_unavailable(extraction):
    import base64

    service, state, run = extraction
    item = SimpleNamespace(source_url="https://studio.test/work/current", thumbnail=None)
    gallery = [{"image": "data:image/png;base64," + base64.b64encode(raster(color)).decode(),
                "source_url": f"https://studio.test/{index}.png", "basis": "Saved project image",
                "preview_version": 6} for index, color in enumerate(("red", "blue", "green"))]
    service.store.save_preview(service.cache_key(item), {**gallery[0], "images": gallery}, 3600)
    state["page_status"] = 403
    result = run()
    assert [image["source_url"] for image in result["images"]] == [image["source_url"] for image in gallery]
    assert all(image["preview_version"] == PREVIEW_VERSION for image in result["images"])


def test_old_thumbnail_metadata_cannot_override_project_media_with_a_generic_logo(extraction):
    _, state, run = extraction
    state["thumbnail"] = ApprovedThumbnail(
        url="https://studio.test/images/social.jpg", source_url="https://studio.test/work/current",
        available=True, usage_status="approved",
    )
    state["html"] = '<article><img src="/frame.png"></article>'
    state["assets"][("studio.test", "/frame.png")] = (200, "image/png", raster("red"))
    assert run()["source_url"] == "https://studio.test/frame.png"
    assert ("studio.test", "/images/social.jpg") not in state["requests"]


def test_responsive_variant_is_preferred_over_small_lazy_placeholder():
    parser = PreviewHTML("https://studio.test/work/current")
    parser.feed('<article><img data-src="/frame.jpg?w=32" '
                'data-srcset="/frame.jpg?w=640 640w, /frame.jpg?w=1280 1280w"></article>')
    parser.extract()
    assert [candidate.url for candidate in parser.candidates()] == ["https://studio.test/frame.jpg?w=1280"]
