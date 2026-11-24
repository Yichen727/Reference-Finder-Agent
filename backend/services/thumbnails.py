"""Bounded, cached previews from official metadata, never guessed image URLs."""

import base64
import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from itertools import chain
from math import ceil
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from backend.services.page_verification import CheckStopped, PublicHTML
from backend.services.preview_candidates import decorative_url
from backend.services.preview_fetching import PreviewAccess, PreviewFetcher
from backend.services.preview_images import PreviewHTML, clean_saved_preview, usable_saved_preview
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
PREVIEW_VERSION = 7
MAX_PREVIEW_IMAGES = 4
MAX_BODY_CANDIDATES = 8
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


def website_images(parser, approved_domains, title=""):
    """Rank media by the current work's context before downloading it."""
    candidates, seen = [], set()
    for candidate in parser.candidates(title)[:MAX_BODY_CANDIDATES]:
        image_url = candidate.url
        basis = candidate.basis
        license_url = licensed_image(parser, image_url)
        if license_url:
            basis += ". Image license: " + license_url
        elif domain_matches(parser.base_url, approved_domains):
            basis = "Website preview permission configured by workspace administrator"
        if image_url not in seen:
            seen.add(image_url)
            candidates.append((image_url, basis, candidate.kind != "metadata",
                               candidate.score, candidate.kind))
    return candidates


def preview_failure(error):
    return (
        error.reason
        if isinstance(error, CheckStopped)
        else ("The preview source could not be reached or returned invalid image data.")
    )


def website_previews(fetcher, item, source, approved_domains, result):
    """Keep the official video cover first, then collect the original page's gallery."""
    page_url, _, body = fetcher.fetch(source, {"text/html", "application/xhtml+xml"}, 2_000_000)
    parser = PreviewHTML(page_url, title=getattr(item, "title", ""))
    parser.feed(body.decode("utf-8", errors="replace"))
    parser.extract()
    embedded = single_embedded_provider(parser.media)
    if not embedded and not parser.collector.has_media_markup:
        embedded = verified_embedded_provider(item, source)
    if embedded:
        try:
            image_url, basis = provider_cover(fetcher, embedded, embedded=True)
            yield image_url, basis, False, 90, "provider"
        except PREVIEW_ERRORS as error:
            result["reason"] = preview_failure(error)
    candidates = website_images(parser, approved_domains, getattr(item, "title", ""))
    result.setdefault("diagnostics", []).append({
        "stage": "extraction", "candidates": len(candidates),
        "rejected": dict(parser.collector.rejections), "has_video_markup": parser.collector.has_media_markup,
    })
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
            or image.width * image.height > 12_000_000
        ):
            raise CheckStopped("unverified", "Image format or dimensions are unsupported.")
        if image.width < 80 or image.height < 45:
            raise CheckStopped("unverified", "Preview image is too small.")
        if from_body and (image.width < 160 or image.height < 90):
            raise CheckStopped("unverified", "Article image is too small for a reference preview.")
        compact = len(body) <= MAX_PREVIEW_BYTES and max(image.size) <= MAX_PREVIEW_DIMENSION
        mime = IMAGE_TYPES[image.format]
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
    final, declared_mime, body = fetcher.fetch(image_url, set(IMAGE_TYPES.values()), MAX_IMAGE_BYTES)
    mime, body = compact_preview(body, declared_mime, from_body=from_body)
    with Image.open(BytesIO(body)) as image:
        width, height = image.size
    return {
        "image": f"data:{mime};base64," + base64.b64encode(body).decode("ascii"),
        "source_url": final,
        "basis": basis,
        "preview_version": PREVIEW_VERSION,
        "declared_mime": declared_mime,
        "width": width, "height": height,
    }


def image_fingerprint(preview):
    """Collapse duplicate pixels even when the page uses several CDN URLs."""
    raw = base64.b64decode(preview["image"].split(",", 1)[1], validate=True)
    with Image.open(BytesIO(raw)) as image:
        normalized = ImageOps.exif_transpose(image).convert("RGBA").resize((64, 64))
        return hashlib.sha256(normalized.tobytes()).hexdigest()


class ThumbnailService:
    def __init__(self, settings, store, *, fetcher_factory=PreviewFetcher):
        self.settings, self.store, self.fetcher_factory = settings, store, fetcher_factory
        self.lock = threading.Lock()
        self.access = PreviewAccess()

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
            cached = clean_saved_preview(self.store.preview(self.cache_key(item)))
            if not usable_saved_preview(cached):
                continue
            previews[item.id] = cached
        return previews

    def resolve(self, item, *, deadline=None) -> dict:
        source = str(item.source_url)
        approved = item.thumbnail.model_dump(mode="json") if item.thumbnail else None
        key = self.cache_key(item)
        cached = self.store.preview(key)
        if cached and cached.get("image"):
            cached = clean_saved_preview(cached) or cached
        seeds = []
        if cached is not None:
            if cached.get("preview_version") == PREVIEW_VERSION and (
                not cached.get("image") or usable_saved_preview(cached)
            ):
                return cached
            for old in (cached.get("images") or [cached])[:MAX_PREVIEW_IMAGES]:
                if not usable_saved_preview(old):
                    continue
                try:
                    header, encoded = old["image"].split(",", 1)
                    mime = header.removeprefix("data:").removesuffix(";base64")
                    mime, body = compact_preview(base64.b64decode(encoded, validate=True), mime)
                    seed = {
                        **old,
                        "image": f"data:{mime};base64," + base64.b64encode(body).decode("ascii"),
                        "preview_version": PREVIEW_VERSION,
                    }
                    seed.pop("images", None)
                    seeds.append(seed)
                except PREVIEW_ERRORS:
                    pass
            if seeds and video_metadata_url(source):
                result = {**seeds[0], "images": seeds}
                self.store.save_preview(key, result, 86400)
                return result
        result = {
            **PLACEHOLDER,
            "images": [],
            "preview_version": PREVIEW_VERSION,
            "reason": "No usable source-declared preview was found.",
        }
        images, seen_urls, seen_pixels = [], set(), set()
        errors = []

        def add_image(preview):
            url = canonical_url(preview.get("source_url") or "")
            pixels = image_fingerprint(preview)
            if url in seen_urls or pixels in seen_pixels:
                for old in images:
                    if old.get("_fingerprint") == pixels:
                        old["score"] = max(old.get("score", 20), preview.get("score", 20))
                return
            seen_urls.add(url)
            seen_pixels.add(pixels)
            images.append({**preview, "_fingerprint": pixels})

        for seed in seeds:
            try:
                add_image(seed)
            except PREVIEW_ERRORS:
                pass
        try:
            fetcher = self.fetcher_factory(self.settings)
            fetcher.access = self.access
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
                and not decorative_url(approved["url"])
            ):
                image_url, basis = approved["url"], "Previously approved preview metadata"
            elif provider:
                image_url, basis = provider_cover(fetcher, provider)
            if not provider and item.platform not in {"YouTube", "Vimeo"}:
                candidates = website_previews(
                    fetcher, item, source, self.settings.preview_approved_domains, result
                )
            if image_url:
                candidates = chain([(image_url, basis, False, 95, "provider" if provider else "approved")],
                                   candidates)
            for image_url, basis, from_body, score, kind in candidates:
                if canonical_url(image_url) in seen_urls:
                    continue
                try:
                    preview = image_preview(fetcher, image_url, basis, from_body=from_body)
                    quality = min(4, preview["width"] * preview["height"] / 250_000)
                    add_image({**preview, "score": round(score + quality, 2), "source_kind": kind,
                               "source_page_url": source})
                    if len(images) >= MAX_PREVIEW_IMAGES + len(seeds):
                        break
                except PREVIEW_ERRORS as error:
                    result["reason"] = preview_failure(error)
                    errors.append({"stage": "image", "url": image_url, "reason": preview_failure(error)})
        except PREVIEW_ERRORS as error:
            result["reason"] = preview_failure(error)
            errors.append({"stage": "extraction", "reason": preview_failure(error)})
        diagnostics = result.get("diagnostics", [])
        if images:
            images.sort(key=lambda preview: -preview.get("score", 20))
            images = images[:MAX_PREVIEW_IMAGES]
            for preview in images:
                preview.pop("_fingerprint", None)
            result = {**images[0], "images": images}
        access_notes = getattr(locals().get("fetcher"), "diagnostics", [])
        result["diagnostics"] = (diagnostics + errors + access_notes)[-24:]
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
