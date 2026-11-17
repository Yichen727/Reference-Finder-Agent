"""Reference rendering helpers; live workspace styles are in workspace.css."""

import re
from html import escape


def borrow_summary(value: str) -> str:
    """Keep card copy to at most two sentences without changing stored annotations."""
    text = " ".join(value.split())
    sentences = re.split(r"(?<=[.!?])\s+", text)
    summary = " ".join(sentences[:2])
    if len(summary) > 280:
        summary = summary[:277].rsplit(" ", 1)[0] + "..."
    return summary


def preview_markup(preview: dict | None, title: str) -> str:
    """Render only backend-validated raster bytes; never inject third-party markup."""
    image = (preview or {}).get("image") or ""
    if re.fullmatch(r"data:image/(?:jpeg|png|webp|gif);base64,[A-Za-z0-9+/=]+", image):
        content = f'<img src="{image}" alt="{escape(title, quote=True)} preview" />'
    else:
        content = '<div class="rf-placeholder"><span aria-hidden="true">▧</span>Preview unavailable</div>'
    return f'<div class="rf-preview">{content}</div>'
