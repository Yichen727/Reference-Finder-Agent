"""Small offline Streamlit component with ID-based dragging and per-row removal."""

from hashlib import sha256
from pathlib import Path

import streamlit as st

ASSETS = Path(__file__).with_name("pack_assets")


@st.cache_resource(show_spinner=False)
def _renderer(css: str, js: str):
    # A new asset revision must also replace the browser's registered component.
    revision = sha256((css + js).encode("utf-8")).hexdigest()[:12]
    return st.components.v2.component(
        f"atomized_pack_list_{revision}",
        html=(
            '<div class="pack-list" role="list" aria-label="Selected references"></div>'
            '<p class="sr-status" role="status" aria-live="polite"></p>'
        ),
        css=css,
        js=js,
    )


def pack_list(*, data, key, on_action_change):
    renderer = _renderer(
        (ASSETS / "pack.css").read_text(encoding="utf-8"),
        (ASSETS / "pack.js").read_text(encoding="utf-8"),
    )
    return renderer(data=data, key=key, on_action_change=on_action_change)
