import copy
import json
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from backend.repositories.sqlite import Repository
from backend.schemas.models import StoredBrief
from backend.services.presentation import PresentationStore, research_title
from backend.services.thumbnails import PreviewFetcher, ThumbnailService, video_metadata_url
from frontend.curation import apply_pack_action
from tests.conftest import completed_run
from tests.fixtures import FixtureProvider
from tests.test_page_verification import PUBLIC_IP, public_dns, response


@pytest.fixture
def preview_environment(settings):
    repo = Repository(settings.data_dir / "previews.sqlite3")
    repo.initialize()
    store = PresentationStore(repo)
    store.initialize()
    output = BytesIO()
    Image.new("RGB", (160, 90), "#557766").save(output, format="PNG")
    state = {
        "requests": [],
        "image": output.getvalue(),
        "image_url": "https://i.ytimg.com/vi/abc123xyz00/hqdefault.jpg",
        "html": "",
        "denied": False,
        "redirect": None,
    }

    def handler(request):
        state["requests"].append(request)
        if request.url.path == "/robots.txt":
            return response(
                body=b"User-agent: *\n" + (b"Disallow: /" if state["denied"] else b"Allow: /"),
                headers={"content-type": "text/plain"},
            )
        if "oembed" in request.url.path:
            return response(
                body=json.dumps({"type": "video", "thumbnail_url": state["image_url"]}).encode(),
                headers={"content-type": "application/json"},
            )
        if request.url.path.startswith("/work/"):
            return response(body=state["html"].encode())
        if state["redirect"]:
            return response(status=302, headers={"location": state["redirect"]})
        return response(body=state["image"], headers={"content-type": "image/png"})

    def factory(config):
        return PreviewFetcher(config, resolver=public_dns, transport=httpx.MockTransport(handler))

    service = ThumbnailService(settings, store, fetcher_factory=factory)
    provider = FixtureProvider()
    brief = StoredBrief(concept="Human side of AI", requested_count=1)
    item = provider.search(brief, provider.strategy(brief)).results[0]
    item.source_url = "https://www.youtube.com/watch?v=abc123xyz00"
    item.platform = "YouTube"
    return service, item, state


@pytest.mark.parametrize("platform", ["youtube", "vimeo"])
def test_official_metadata_cover_is_validated_and_cached(preview_environment, platform):
    service, item, state = preview_environment
    if platform == "vimeo":
        item.source_url = "https://vimeo.com/123456789?h=access-hash"
        item.platform = "Vimeo"
        state["image_url"] = "https://i.vimeocdn.com/video/123456789.jpg"
    result = service.resolve(item)
    assert result["image"].startswith("data:image/png;base64,")
    assert "oEmbed" in result["basis"]
    assert result["source_url"] == state["image_url"]
    calls = len(state["requests"])
    assert service.resolve(item) == result
    run = SimpleNamespace(results=[item], selection=SimpleNamespace(ordered_ids=[item.id]))
    assert service.for_export(run) == {item.id: result}
    assert len(state["requests"]) == calls
    assert all(request.url.host == PUBLIC_IP for request in state["requests"])
    assert all(
        "authorization" not in request.headers and "cookie" not in request.headers
        for request in state["requests"]
    )


@pytest.mark.parametrize("mode", ["private", "corrupt", "robots", "redirect", "wrong_host"])
def test_unsafe_restricted_or_invalid_images_become_cached_placeholders(preview_environment, mode):
    service, item, state = preview_environment
    if mode == "private":
        state["image_url"] = "http://127.0.0.1/secret.png"
    elif mode == "corrupt":
        state["image"] = b"not an image"
    elif mode == "robots":
        state["denied"] = True
    elif mode == "redirect":
        state["redirect"] = "http://127.0.0.1/secret.png"
    else:
        state["image_url"] = "https://attacker.test/cover.png"
    result = service.resolve(item)
    assert result["image"] is None
    calls = len(state["requests"])
    assert service.resolve(item)["image"] is None
    assert len(state["requests"]) == calls
    assert all(request.headers["host"] != "127.0.0.1" for request in state["requests"])


@pytest.mark.parametrize(
    "permission", ["unknown", "configured", "licensed", "unrelated_license", "noncommercial"]
)
def test_og_image_requires_specific_permission(preview_environment, permission):
    service, item, state = preview_environment
    item.source_url = "https://studio.test/work/futures"
    item.platform = "Studio portfolios"
    state["html"] = '<meta property="og:image" content="https://studio.test/cover.png">'
    if permission == "configured":
        service.settings.preview_approved_domains = ["studio.test"]
    elif permission in {"licensed", "unrelated_license", "noncommercial"}:
        state["html"] += (
            '<script type="application/ld+json">'
            + json.dumps(
                {
                    "@type": "ImageObject",
                    "contentUrl": "https://studio.test/other.png"
                    if permission == "unrelated_license"
                    else "https://studio.test/cover.png",
                    "license": "https://creativecommons.org/licenses/by-nc/4.0/"
                    if permission == "noncommercial"
                    else "https://creativecommons.org/licenses/by/4.0/",
                }
            )
            + "</script>"
        )
    result = service.resolve(item)
    assert bool(result["image"]) == (permission in {"configured", "licensed"})
    if permission in {"unknown", "unrelated_license", "noncommercial"}:
        assert not any(request.url.path == "/cover.png" for request in state["requests"])


def test_vimeo_metadata_preserves_unlisted_access_hash():
    endpoint, _, _ = video_metadata_url("https://vimeo.com/123456789?h=access-hash")
    assert "access-hash" in endpoint
    assert video_metadata_url("https://youtube.com.attacker.test/watch?v=abc123xyz00") is None


@pytest.mark.parametrize(
    "prompt",
    [
        "AI",
        "Luxury automotive",
        "Create a short film about the human side of AI with real people.",
        "Sustainable Futures Campaign",
        "",
        "A" * 6000,
    ],
)
def test_titles_stay_within_three_to_six_words(prompt):
    title = research_title(prompt)
    assert 3 <= len(title.split()) <= 6
    assert "\n" not in title


def test_recent_title_uses_original_prompt_and_survives_restart(client, monkeypatch):
    run, _ = completed_run(client)
    brief = client.app.state.workflow.brief(run["brief_id"])
    brief.concept = "Create a short film about the human side of AI with warmth."
    client.app.state.workflow.repository.put("brief", brief)
    before = client.get(f"/search-runs/{run['id']}").json()
    title = client.get("/search-runs").json()[0]["title"]
    assert title == "Human Side of AI"
    monkeypatch.setattr(
        "backend.services.presentation.research_title", lambda prompt: pytest.fail("Title must be persisted")
    )
    replacement = PresentationStore(client.app.state.workflow.repository)
    replacement.initialize()
    assert replacement.title(before) == title
    assert client.get("/search-runs").json()[0]["title"] == title
    assert client.get(f"/search-runs/{run['id']}").json() == before


@pytest.mark.parametrize(
    "action",
    [
        {"revision": 2, "ordered_ids": ["second", "first"]},
        {"revision": 3, "ordered_ids": ["first", "first"]},
        {"revision": 3, "ordered_ids": ["first", "unknown"]},
        {"revision": 3, "ordered_ids": ["first"]},
        {"revision": 3, "ordered_ids": ["first"], "removed_id": "unknown"},
    ],
)
def test_stale_or_invalid_pack_events_never_change_selection(action):
    draft = {"ordered_ids": ["first", "second"], "revision": 3}
    before = copy.deepcopy(draft)
    assert not apply_pack_action(draft, action)
    assert draft == before


def test_id_based_pack_events_support_duplicates_in_titles_and_explicit_removal():
    draft = {"ordered_ids": ["first", "second"], "revision": 3}
    assert apply_pack_action(draft, {"revision": 3, "ordered_ids": ["second", "first"]})
    assert draft["ordered_ids"] == ["second", "first"]
    assert apply_pack_action(draft, {"revision": 3, "ordered_ids": ["second"], "removed_id": "first"})
    assert draft["ordered_ids"] == ["second"]
