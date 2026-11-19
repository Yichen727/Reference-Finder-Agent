"""Preview recovery and source association, without live network or OpenAI calls."""

import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from backend.schemas.models import PageCheck
from backend.services.thumbnails import (
    FAILED_PREVIEW_TTL,
    PREVIEW_VERSION,
    single_embedded_provider,
    video_metadata_url,
)
from tests.test_presentation import preview_environment as preview_environment


def source_page(item, *media):
    item.source_url = "https://studio.test/work/futures"
    item.platform = "studio.test"
    item.page_check = PageCheck(
        original_url=str(item.source_url),
        final_url=item.source_url,
        status="accessible",
        work_page=True,
        media_urls=list(media),
    )


@pytest.mark.parametrize(
    "video",
    [
        "https://www.youtube.com/embed/abc123xyz00?feature=oembed",
        "https://www.youtube-nocookie.com/embed/abc123xyz00",
        "https://player.vimeo.com/video/123456789?h=access-hash&dnt=1",
    ],
)
def test_verified_single_embedded_video_uses_official_cover_without_refetching_article(
    preview_environment, video
):
    service, item, state = preview_environment
    source_page(item, video)
    if "vimeo" in video:
        state["image_url"] = "https://i.vimeocdn.com/video/123456789.jpg"
    result = service.resolve(item)
    assert result["image"].startswith("data:image/png;base64,")
    assert "embedded video" in result["basis"]
    assert all(request.url.path != "/work/futures" for request in state["requests"])
    calls = len(state["requests"])
    assert service.resolve(item) == result
    assert len(state["requests"]) == calls
    run = SimpleNamespace(results=[item], selection=SimpleNamespace(ordered_ids=[item.id]))
    assert service.for_export(run) == {item.id: result}
    assert len(state["requests"]) == calls


@pytest.mark.parametrize("kind", ["iframe", "jsonld"])
def test_article_without_stored_page_check_discovers_embedded_cover(preview_environment, kind):
    service, item, state = preview_environment
    source_page(item)
    item.page_check = None
    video = "https://www.youtube.com/embed/abc123xyz00"
    state["html"] = (
        f'<iframe src="{video}"></iframe>'
        if kind == "iframe"
        else (
            '<script type="application/ld+json">'
            + json.dumps({"@type": "VideoObject", "embedUrl": video})
            + "</script>"
        )
    )
    result = service.resolve(item)
    assert result["image"] and "embedded video" in result["basis"]


def test_multiple_unrelated_embeds_are_not_assigned_an_arbitrary_cover(preview_environment):
    service, item, state = preview_environment
    source_page(item, "https://youtube.com/watch?v=abc123xyz00", "https://vimeo.com/123456789")
    state["html"] = (
        '<iframe src="https://youtube.com/embed/abc123xyz00"></iframe>'
        '<iframe src="https://player.vimeo.com/video/123456789"></iframe>'
    )
    result = service.resolve(item)
    assert not result["image"]
    assert "several videos" in result["reason"]
    assert not any("oembed" in request.url.path for request in state["requests"])


def test_equivalent_embeds_of_one_video_are_deduplicated():
    provider = single_embedded_provider(
        [
            "https://youtu.be/abc123xyz00",
            "https://www.youtube-nocookie.com/embed/abc123xyz00?rel=0",
        ]
    )
    assert provider and "youtube.com/oembed" in provider[0]


@pytest.mark.parametrize("mode", ["unrelated", "unverified", "not_work"])
def test_stored_media_must_belong_to_a_verified_work_page(preview_environment, mode):
    service, item, state = preview_environment
    source_page(item, "https://youtube.com/watch?v=abc123xyz00")
    if mode == "unrelated":
        item.page_check.original_url = "https://unrelated.test/work/other"
        item.page_check.final_url = "https://unrelated.test/work/other"
    elif mode == "unverified":
        item.page_check.status = "unverified"
    else:
        item.page_check.work_page = False
    assert not service.resolve(item)["image"]
    assert not any("oembed" in request.url.path for request in state["requests"])


def test_old_failed_cache_is_retried_but_successful_cache_is_reused(preview_environment):
    service, item, state = preview_environment
    key = service.cache_key(item)
    service.store.save_preview(key, {"image": None, "source_url": None, "basis": "Old failure"}, 3600)
    result = service.resolve(item)
    assert result["image"] and state["requests"]
    state["requests"].clear()
    assert service.resolve(item) == result
    assert not state["requests"]


def test_failure_reason_and_short_retry_ttl_are_persisted(preview_environment, monkeypatch):
    service, item, state = preview_environment
    source_page(item)
    state["html"] = '<meta property="og:image" content="/cover.png">'
    state["image_statuses"] = {"/cover.png": 404}
    saved = []
    save = service.store.save_preview

    def record(key, value, ttl):
        saved.append(ttl)
        save(key, value, ttl)

    monkeypatch.setattr(service.store, "save_preview", record)
    result = service.resolve(item)
    assert not result["image"]
    assert result["reason"] == "Preview source returned HTTP 404."
    assert result["preview_version"] == PREVIEW_VERSION
    assert saved == [FAILED_PREVIEW_TTL]
    calls = len(state["requests"])
    service.resolve(item)
    assert len(state["requests"]) == calls


def test_previous_permission_failure_is_retried_immediately(preview_environment):
    service, item, state = preview_environment
    source_page(item)
    state["html"] = '<meta property="og:image" content="/cover.png">'
    service.store.save_preview(
        service.cache_key(item),
        {"image": None, "preview_version": 3, "reason": "Website image permission has not been established."},
        3600,
    )
    result = service.resolve(item)
    assert result["image"] and result["preview_version"] == PREVIEW_VERSION


@pytest.mark.parametrize(
    "video",
    [
        "https://vimeo.com/123456789/access-hash",
        "https://vimeo.com/123456789?h=access-hash",
        "https://player.vimeo.com/video/123456789?h=access-hash&autoplay=1",
    ],
)
def test_vimeo_player_urls_preserve_privacy_hash_in_canonical_oembed_link(video):
    endpoint, _, _ = video_metadata_url(video)
    assert parse_qs(urlsplit(endpoint).query)["url"] == ["https://vimeo.com/123456789/access-hash"]


@pytest.mark.parametrize(
    "url",
    [
        "https://vimeo.com/showcase/123456789",
        "https://youtube.com.attacker.test/embed/abc123xyz00",
        "https://vimeo.com/123456789?h=hash/unsafe",
        "https://studio.test/work/video",
    ],
)
def test_non_video_or_spoofed_sources_do_not_create_provider_requests(url):
    assert video_metadata_url(url) is None


def test_later_worker_groups_receive_time_within_the_batch_budget(preview_environment, monkeypatch):
    service, _, _ = preview_environment
    deadlines = []
    monkeypatch.setattr("backend.services.thumbnails.time.monotonic", lambda: 100)
    monkeypatch.setattr(service, "resolve", lambda item, deadline=None: deadlines.append(deadline) or {})
    run = SimpleNamespace(results=[SimpleNamespace(id=str(index)) for index in range(20)])
    assert len(service.for_run(run)) == 20
    assert deadlines == [160] * 20
