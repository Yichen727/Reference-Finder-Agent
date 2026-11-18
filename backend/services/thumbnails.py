"""Bounded, cached previews from official metadata, never guessed image URLs."""

import base64
import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from math import ceil
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import httpx
from PIL import Image, UnidentifiedImageError

from backend.services.page_verification import USER_AGENT, CheckStopped, PageVerifier, PublicHTML
from backend.services.source_policy import canonical_url, domain_matches

PLACEHOLDER = {"image": None, "source_url": None, "basis": "Preview unavailable or permission unconfirmed."}
IMAGE_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif"}
PREVIEW_VERSION = 2
FAILED_PREVIEW_TTL = 120


class PreviewFetcher(PageVerifier):
    def __init__(self, settings, **kwargs):
        super().__init__(settings, **kwargs)
        self.deadline = time.monotonic() + 12

    def fetch(self, url: str, types: set[str], limit: int):
        current = url
        for _ in range(4):
            clean, target, host, authority = self.pinned_url(current)
            self.permitted(clean)
            with self.lock:
                if self.requests >= 12:
                    raise CheckStopped("unverified", "Preview request budget reached.")
                self.requests += 1
            with httpx.Client(
                trust_env=False,
                follow_redirects=False,
                transport=self.transport,
                timeout=min(4, self.remaining()),
            ) as client:
                with client.stream(
                    "GET",
                    target,
                    extensions={"sni_hostname": host},
                    headers={
                        "Host": authority,
                        "User-Agent": USER_AGENT,
                        "Accept-Encoding": "identity",
                        "Accept": ", ".join(sorted(types)),
                    },
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            break
                        current = urljoin(clean, location)
                        continue
                    if response.status_code == 429:
                        self.rate_limited.add(
                            str(httpx.URL(clean).copy_with(path="/", query=None)).rstrip("/")
                        )
                    if response.status_code != 200:
                        raise CheckStopped(
                            "restricted", f"Preview source returned HTTP {response.status_code}."
                        )
                    mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if mime not in types or response.headers.get("content-encoding", "identity") not in {
                        "",
                        "identity",
                    }:
                        raise CheckStopped("unverified", "Unsupported preview response.")
                    size = response.headers.get("content-length", "")
                    if size.isdigit() and int(size) > limit:
                        raise CheckStopped("unverified", "Preview is too large.")
                    body = bytearray()
                    for chunk in response.iter_raw():
                        self.remaining()
                        body.extend(chunk)
                        if len(body) > limit:
                            raise CheckStopped("unverified", "Preview is too large.")
                    return clean, mime, bytes(body)
        raise CheckStopped("unverified", "Preview redirect limit reached.")


def video_metadata_url(source: str):
    parts = urlsplit(source)
    host = (parts.hostname or "").lower()
    if host in {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }:
        video_id = parts.path.strip("/") if host == "youtu.be" else parse_qs(parts.query).get("v", [""])[0]
        if not video_id and re.match(r"^/(?:shorts|embed|live)/", parts.path):
            video_id = parts.path.split("/")[2]
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            url = "https://www.youtube.com/watch?" + urlencode({"v": video_id})
            return (
                "https://www.youtube.com/oembed?" + urlencode({"url": url, "format": "json"}),
                ["ytimg.com"],
                "YouTube oEmbed preview",
            )
    if host in {"vimeo.com", "www.vimeo.com", "player.vimeo.com"}:
        match = re.fullmatch(
            r"/(?:video/|channels/[^/]+/|groups/[^/]+/videos/)?(\d+)(?:/([A-Za-z0-9_-]+))?/?",
            parts.path,
        )
        if not match:
            return None
        # oEmbed requires the actual video link, including any supplied privacy hash.
        video_id, path_hash = match.groups()
        privacy_hash = path_hash or parse_qs(parts.query).get("h", [""])[0]
        if privacy_hash and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", privacy_hash):
            return None
        video_url = f"https://vimeo.com/{video_id}" + (f"/{privacy_hash}" if privacy_hash else "")
        return (
            "https://vimeo.com/api/oembed.json?" + urlencode({"url": video_url, "maxwidth": 640}),
            ["vimeocdn.com"],
            "Vimeo oEmbed preview",
        )
    return None


def single_embedded_provider(urls):
    """Never choose an arbitrary film from a page containing several videos."""
    providers = {}
    for url in urls:
        clean = canonical_url(str(url))
        if clean and urlsplit(clean).port in {None, 80, 443}:
            provider = video_metadata_url(clean)
            if provider:
                providers[provider[0]] = provider
    return next(iter(providers.values())) if len(providers) == 1 else None


def verified_embedded_provider(item, source):
    page = item.page_check
    if not page or page.status != "accessible" or not page.work_page:
        return None
    if canonical_url(source) not in {
        canonical_url(str(page.original_url)),
        canonical_url(str(page.final_url or "")),
    }:
        return None
    return single_embedded_provider(page.media_urls)


def provider_cover(fetcher, provider, *, embedded=False):
    endpoint, domains, basis = provider
    _, _, body = fetcher.fetch(endpoint, {"application/json"}, 100_000)
    metadata = json.loads(body)
    if not isinstance(metadata, dict):
        raise CheckStopped("unverified", "Invalid preview metadata.")
    image_url = canonical_url(str(metadata.get("thumbnail_url", "")))
    if metadata.get("type") != "video" or not image_url or not domain_matches(image_url, domains):
        raise CheckStopped("unverified", "Official preview metadata did not provide a valid cover.")
    if embedded:
        basis += " for the source page's embedded video"
    return image_url, basis


def licensed_image(parser: PublicHTML, image_url: str) -> str | None:
    """Only an image-specific public license establishes automatic website permission."""

    def walk(value, depth=0):
        if depth > 8:
            return None
        if isinstance(value, list):
            return next((found for item in value[:50] if (found := walk(item, depth + 1))), None)
        if not isinstance(value, dict):
            return None
        if value.get("@type") == "ImageObject":
            candidate = canonical_url(
                urljoin(parser.base_url, str(value.get("contentUrl") or value.get("url") or ""))
            )
            license_url = str(value.get("license") or "")
            if candidate == image_url and re.fullmatch(
                r"https://creativecommons\.org/(?:licenses/(?:by|by-sa)/(?:[1-4]\.0|2\.5)|publicdomain/zero/1\.0)/?",
                license_url,
            ):
                return license_url
        return next((found for item in value.values() if (found := walk(item, depth + 1))), None)

    for block in parser.ld[:8]:
        try:
            found = walk(json.loads(block))
            if found:
                return found
        except (ValueError, RecursionError):
            pass
    return None


class ThumbnailService:
    def __init__(self, settings, store, *, fetcher_factory=PreviewFetcher):
        self.settings, self.store, self.fetcher_factory = settings, store, fetcher_factory
        self.lock = threading.Lock()

    def cache_key(self, item):
        source = str(item.source_url)
        approved = item.thumbnail.model_dump(mode="json") if item.thumbnail else None
        return hashlib.sha256(
            json.dumps([source, approved, self.settings.preview_approved_domains]).encode()
        ).hexdigest()

    def for_export(self, run) -> dict:
        """Place the card's validated, permitted preview in the user's reference pack.

        Preview discovery already checks the source and preview permissions. Reuse
        that cache without a second domain allowlist or any export-time downloads;
        retain the source and permission basis for the slide's speaker notes.
        """
        selected = set(run.selection.ordered_ids) if run.selection else set()
        previews = {}
        for item in run.results:
            if item.id not in selected:
                continue
            cached = self.store.preview(self.cache_key(item))
            if not cached or not cached.get("image"):
                continue
            previews[item.id] = cached
        return previews

    def resolve(self, item, *, deadline=None) -> dict:
        source = str(item.source_url)
        approved = item.thumbnail.model_dump(mode="json") if item.thumbnail else None
        key = self.cache_key(item)
        cached = self.store.preview(key)
        if cached is not None and (cached.get("image") or cached.get("preview_version") == PREVIEW_VERSION):
            return cached
        result = {
            **PLACEHOLDER,
            "preview_version": PREVIEW_VERSION,
            "reason": "No permitted preview metadata was found.",
        }
        try:
            fetcher = self.fetcher_factory(self.settings)
            if deadline is not None:
                fetcher.deadline = min(fetcher.deadline, deadline)
            if not canonical_url(source):
                return result
            image_url, basis = None, ""
            provider = video_metadata_url(source)
            if (
                approved
                and approved["available"]
                and canonical_url(approved["source_url"]) == canonical_url(source)
            ):
                image_url, basis = approved["url"], "Previously approved preview metadata"
            elif provider:
                image_url, basis = provider_cover(fetcher, provider)
            elif embedded := verified_embedded_provider(item, source):
                image_url, basis = provider_cover(fetcher, embedded, embedded=True)
            elif item.platform not in {"Instagram", "Pinterest", "YouTube", "Vimeo"}:
                page_url, _, body = fetcher.fetch(source, {"text/html", "application/xhtml+xml"}, 500_000)
                parser = PublicHTML(page_url)
                parser.feed(body.decode("utf-8", errors="replace"))
                parser.extract()
                embedded = single_embedded_provider(parser.media)
                if embedded:
                    image_url, basis = provider_cover(fetcher, embedded, embedded=True)
                else:
                    image_url = (
                        canonical_url(urljoin(page_url, parser.meta.get("og:image", "")))
                        if parser.meta.get("og:image")
                        else None
                    )
                    if image_url:
                        license_url = licensed_image(parser, image_url)
                        if license_url:
                            basis = "Image license: " + license_url
                        elif domain_matches(page_url, self.settings.preview_approved_domains):
                            basis = "Website preview permission configured by workspace administrator"
                        else:
                            image_url = None
                            result["reason"] = "Website image permission has not been established."
                    elif len(parser.media) > 1:
                        result["reason"] = (
                            "The source page contains several videos; no single cover was chosen."
                        )
            if image_url:
                final, mime, body = fetcher.fetch(image_url, set(IMAGE_TYPES.values()), 2_000_000)
                with Image.open(BytesIO(body)) as image:
                    if (
                        image.format not in IMAGE_TYPES
                        or IMAGE_TYPES[image.format] != mime
                        or image.width * image.height > 12_000_000
                    ):
                        raise CheckStopped("unverified", "Image format or dimensions are unsupported.")
                    image.verify()
                result = {
                    "image": f"data:{mime};base64," + base64.b64encode(body).decode("ascii"),
                    "source_url": final,
                    "basis": basis,
                    "preview_version": PREVIEW_VERSION,
                }
        except (
            CheckStopped,
            httpx.HTTPError,
            OSError,
            ValueError,
            TypeError,
            RecursionError,
            KeyError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
        ) as error:
            result["reason"] = (
                error.reason
                if isinstance(error, CheckStopped)
                else ("The preview source could not be reached or returned invalid image data.")
            )
        self.store.save_preview(key, result, 86400 if result["image"] else FAILED_PREVIEW_TTL)
        return result

    def for_run(self, run) -> dict:
        # Serialize enrichment batches to avoid duplicate downloads from concurrent sessions.
        with self.lock:
            # Every queued worker group gets time; later cards must not inherit an
            # already-expired 20-second budget when a pack contains up to 20 items.
            deadline = time.monotonic() + min(60, max(20, ceil(len(run.results) / 4) * 12))
            with ThreadPoolExecutor(max_workers=4) as pool:
                previews = list(pool.map(lambda item: self.resolve(item, deadline=deadline), run.results))
        return {item.id: preview for item, preview in zip(run.results, previews, strict=True)}
