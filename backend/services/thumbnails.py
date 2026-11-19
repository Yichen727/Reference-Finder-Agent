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
from PIL import Image, ImageOps, UnidentifiedImageError

from backend.services.page_verification import USER_AGENT, CheckStopped, PageVerifier, PublicHTML
from backend.services.preview_images import PreviewHTML
from backend.services.source_policy import canonical_url, domain_matches

PLACEHOLDER = {"image": None, "source_url": None, "basis": "Preview unavailable."}
IMAGE_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
    "AVIF": "image/avif",
}
MAX_IMAGE_BYTES = 10_000_000
MAX_PREVIEW_BYTES = 500_000
MAX_PREVIEW_DIMENSION = 1280
PREVIEW_VERSION = 5
FAILED_PREVIEW_TTL = 120
PREVIEW_ERRORS = (
    CheckStopped,
    httpx.HTTPError,
    OSError,
    ValueError,
    TypeError,
    RecursionError,
    KeyError,
    UnidentifiedImageError,
    Image.DecompressionBombError,
)


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
    """Retain matching image-license metadata as attribution, without gating previews."""

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


def website_images(parser, approved_domains):
    """Try the declared preview first, then up to three source-declared body images."""
    candidates, seen = [], set()
    og_image = parser.meta.get("og:image", "")
    preview = canonical_url(urljoin(parser.base_url, og_image)) if og_image else None
    urls = [(preview, False)] if preview else []
    urls.extend((url, True) for url in parser.body_images())
    body_count = 0
    for image_url, from_body in urls:
        if image_url in seen or body_count >= 3:
            continue
        seen.add(image_url)
        basis = "Website-declared preview image"
        license_url = licensed_image(parser, image_url)
        if license_url:
            basis += ". Image license: " + license_url
        elif domain_matches(parser.base_url, approved_domains):
            basis = "Website preview permission configured by workspace administrator"
        if from_body:
            basis = "First suitable article image. " + basis
            body_count += 1
        candidates.append((image_url, basis, from_body))
    return candidates


def preview_failure(error):
    return (
        error.reason
        if isinstance(error, CheckStopped)
        else ("The preview source could not be reached or returned invalid image data.")
    )


def website_previews(fetcher, item, source, approved_domains, result):
    """Load body images only if the source's official video cover is unavailable."""
    embedded = verified_embedded_provider(item, source)
    attempted = set()
    if embedded:
        attempted.add(embedded[0])
        try:
            image_url, basis = provider_cover(fetcher, embedded, embedded=True)
            yield image_url, basis, False
        except PREVIEW_ERRORS as error:
            result["reason"] = preview_failure(error)
    page_url, _, body = fetcher.fetch(source, {"text/html", "application/xhtml+xml"}, 500_000)
    parser = PreviewHTML(page_url)
    parser.feed(body.decode("utf-8", errors="replace"))
    parser.extract()
    embedded = single_embedded_provider(parser.media)
    if embedded and embedded[0] not in attempted:
        try:
            image_url, basis = provider_cover(fetcher, embedded, embedded=True)
            yield image_url, basis, False
        except PREVIEW_ERRORS as error:
            result["reason"] = preview_failure(error)
    candidates = website_images(parser, approved_domains)
    if not candidates and len(parser.media) > 1:
        result["reason"] = "The source page contains several videos; no single cover was chosen."
    yield from candidates


def compact_preview(body, mime, *, from_body=False):
    """Validate source bytes, then bound cached and rendered preview dimensions/size."""
    if len(body) > MAX_IMAGE_BYTES:
        raise CheckStopped("unverified", "Preview source image is too large.")
    with Image.open(BytesIO(body)) as image:
        if (
            image.format not in IMAGE_TYPES
            or IMAGE_TYPES[image.format] != mime
            or image.width * image.height > 12_000_000
        ):
            raise CheckStopped("unverified", "Image format or dimensions are unsupported.")
        if from_body and (image.width < 160 or image.height < 90):
            raise CheckStopped("unverified", "Article image is too small for a reference preview.")
        compact = len(body) <= MAX_PREVIEW_BYTES and max(image.size) <= MAX_PREVIEW_DIMENSION
        image.verify()
    if compact:
        return mime, body
    with Image.open(BytesIO(body)) as image:
        has_alpha = "A" in image.getbands() or "transparency" in image.info
        image = ImageOps.exif_transpose(image).convert("RGBA" if has_alpha else "RGB")
        for edge in (MAX_PREVIEW_DIMENSION, 960, 720, 540):
            image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
            for quality in (82, 65, 45):
                output = BytesIO()
                image.save(output, format="WEBP", quality=quality)
                if output.tell() <= MAX_PREVIEW_BYTES:
                    return "image/webp", output.getvalue()
    raise CheckStopped("unverified", "Preview could not be compressed to the size limit.")


def image_preview(fetcher, image_url, basis, *, from_body=False):
    final, mime, body = fetcher.fetch(image_url, set(IMAGE_TYPES.values()), MAX_IMAGE_BYTES)
    mime, body = compact_preview(body, mime, from_body=from_body)
    return {
        "image": f"data:{mime};base64," + base64.b64encode(body).decode("ascii"),
        "source_url": final,
        "basis": basis,
        "preview_version": PREVIEW_VERSION,
    }


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
        """Place the card's validated preview in the user's reference pack.

        Reuse the source-validated cache without any export-time downloads;
        retain the source and available attribution for the slide's speaker notes.
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
        if cached is not None:
            if cached.get("preview_version") == PREVIEW_VERSION:
                return cached
            if cached.get("image"):
                # Resize existing validated images locally; avoid downloading them again.
                try:
                    header, encoded = cached["image"].split(",", 1)
                    mime = header.removeprefix("data:").removesuffix(";base64")
                    mime, body = compact_preview(base64.b64decode(encoded, validate=True), mime)
                    cached = {
                        **cached,
                        "image": f"data:{mime};base64," + base64.b64encode(body).decode("ascii"),
                        "preview_version": PREVIEW_VERSION,
                    }
                    self.store.save_preview(key, cached, 86400)
                    return cached
                except PREVIEW_ERRORS:
                    pass
        result = {
            **PLACEHOLDER,
            "preview_version": PREVIEW_VERSION,
            "reason": "No usable source-declared preview was found.",
        }
        try:
            fetcher = self.fetcher_factory(self.settings)
            if deadline is not None:
                fetcher.deadline = min(fetcher.deadline, deadline)
            if not canonical_url(source):
                return result
            image_url, basis = None, ""
            candidates = []
            provider = video_metadata_url(source)
            if (
                approved
                and approved["available"]
                and canonical_url(approved["source_url"]) == canonical_url(source)
            ):
                image_url, basis = approved["url"], "Previously approved preview metadata"
            elif provider:
                image_url, basis = provider_cover(fetcher, provider)
            elif item.platform not in {"YouTube", "Vimeo"}:
                candidates = website_previews(
                    fetcher, item, source, self.settings.preview_approved_domains, result
                )
            if image_url:
                candidates.insert(0, (image_url, basis, False))
            for image_url, basis, from_body in candidates:
                try:
                    result = image_preview(fetcher, image_url, basis, from_body=from_body)
                    break
                except PREVIEW_ERRORS as error:
                    result["reason"] = preview_failure(error)
        except PREVIEW_ERRORS as error:
            result["reason"] = preview_failure(error)
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
