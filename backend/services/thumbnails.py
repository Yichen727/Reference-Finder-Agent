"""Bounded, cached previews from official metadata, never guessed image URLs."""

import base64
import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import httpx
from PIL import Image, UnidentifiedImageError

from backend.services.page_verification import USER_AGENT, CheckStopped, PageVerifier, PublicHTML
from backend.services.source_policy import canonical_url, domain_matches

PLACEHOLDER = {"image": None, "source_url": None, "basis": "Preview unavailable or permission unconfirmed."}
IMAGE_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif"}


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
                        raise CheckStopped("restricted", "Preview metadata or image is unavailable.")
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
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        video_id = parts.path.strip("/") if host == "youtu.be" else parse_qs(parts.query).get("v", [""])[0]
        if not video_id and re.match(r"^/(?:shorts|embed)/", parts.path):
            video_id = parts.path.split("/")[2]
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            url = "https://www.youtube.com/watch?" + urlencode({"v": video_id})
            return (
                "https://www.youtube.com/oembed?" + urlencode({"url": url, "format": "json"}),
                ["ytimg.com"],
                "YouTube oEmbed preview",
            )
    if host in {"vimeo.com", "www.vimeo.com", "player.vimeo.com"} and re.search(r"/\d+(?:/|$)", parts.path):
        return (
            "https://vimeo.com/api/oembed.json?" + urlencode({"url": source, "maxwidth": 640}),
            ["vimeocdn.com"],
            "Vimeo oEmbed preview",
        )
    return None


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

    def resolve(self, item, *, deadline=None) -> dict:
        source = str(item.source_url)
        approved = item.thumbnail.model_dump(mode="json") if item.thumbnail else None
        key = hashlib.sha256(
            json.dumps([source, approved, self.settings.preview_approved_domains]).encode()
        ).hexdigest()
        cached = self.store.preview(key)
        if cached is not None:
            return cached
        result = dict(PLACEHOLDER)
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
                endpoint, domains, basis = provider
                _, _, body = fetcher.fetch(endpoint, {"application/json"}, 100_000)
                metadata = json.loads(body)
                if not isinstance(metadata, dict):
                    raise CheckStopped("unverified", "Invalid preview metadata.")
                image_url = canonical_url(str(metadata.get("thumbnail_url", "")))
                if metadata.get("type") != "video" or not image_url or not domain_matches(image_url, domains):
                    raise CheckStopped(
                        "unverified", "Official preview metadata did not provide a valid cover."
                    )
            elif item.platform not in {"Instagram", "Pinterest", "YouTube", "Vimeo"}:
                page_url, _, body = fetcher.fetch(source, {"text/html", "application/xhtml+xml"}, 500_000)
                parser = PublicHTML(page_url)
                parser.feed(body.decode("utf-8", errors="replace"))
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
        ):
            pass
        self.store.save_preview(key, result, 86400 if result["image"] else 3600)
        return result

    def for_run(self, run) -> dict:
        # Serialize enrichment batches to avoid duplicate downloads from concurrent sessions.
        with self.lock:
            deadline = time.monotonic() + 20
            with ThreadPoolExecutor(max_workers=4) as pool:
                previews = list(pool.map(lambda item: self.resolve(item, deadline=deadline), run.results))
        return {item.id: preview for item, preview in zip(run.results, previews, strict=True)}
