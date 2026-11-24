"""Reference rendering helpers; live workspace styles are in workspace.css."""

import re
from html import escape


def description_summary(value: str | None) -> str:
    """Shorten an existing source description; never infer missing work details."""
    text = " ".join((value or "").split())
    if not text:
        return "Description unavailable. Open the original link for details."
    sentences = re.split(r"(?<=[.!?])\s+", text)
    summary = " ".join(sentences[:2])
    if len(summary) > 280:
        summary = summary[:277].rsplit(" ", 1)[0] + "..."
    return summary


def preview_markup(preview: dict | None, title: str) -> str:
    """Render only backend-validated raster bytes; never inject third-party markup."""
    image = (preview or {}).get("image") or ""
    if re.fullmatch(r"data:image/(?:jpeg|png|webp|gif|avif);base64,[A-Za-z0-9+/=]+", image):
        content = f'<img src="{image}" alt="{escape(title, quote=True)} preview" />'
    else:
        content = '<div class="rf-placeholder"><span aria-hidden="true">▧</span>Preview unavailable</div>'
    return f'<div class="rf-preview">{content}</div>'


def preview_images(preview: dict | None) -> list[dict]:
    """Accept only validated raster payloads, including historical single covers."""
    preview = preview or {}
    candidates = preview.get("images") or [preview]
    images, seen = [], set()
    for candidate in candidates:
        image = candidate.get("image") if isinstance(candidate, dict) else None
        if not isinstance(image, str) or len(image) > 700_000 or image in seen:
            continue
        if not re.fullmatch(r"data:image/(?:jpeg|png|webp|gif|avif);base64,[A-Za-z0-9+/=]+", image):
            continue
        images.append({"image": image})
        seen.add(image)
        if len(images) == 4:
            break
    if not images and preview.get("images"):
        return preview_images({"image": preview.get("image")})
    return images
