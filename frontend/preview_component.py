"""Cached, client-only image browsing in cards and read-only reference details."""

from hashlib import sha256
from pathlib import Path

import streamlit as st

from frontend import layout

ASSETS = Path(__file__).with_name("preview_assets")


@st.cache_resource(show_spinner=False)
def _renderer(css: str, js: str):
    revision = sha256((css + js).encode()).hexdigest()[:12]
    return st.components.v2.component(
        f"atomized_preview_{revision}",
        html='<section class="preview" aria-roledescription="carousel"></section>',
        css=css, js=js,
    )


def render_preview(preview, title, *, key, detail=False):
    images = layout.preview_images(preview)
    if len(images) < 2:
        st.html(layout.preview_markup(images[0] if images else None, title))
        return
    renderer = _renderer((ASSETS / "preview.css").read_text(encoding="utf-8"),
                         (ASSETS / "preview.js").read_text(encoding="utf-8"))
    renderer(data={"images": images, "title": title, "detail": detail}, key=key)
