"""Small offline Streamlit component with ID-based dragging and per-row removal."""

from pathlib import Path

import streamlit as st

ASSETS = Path(__file__).with_name("pack_assets")
pack_list = st.components.v2.component(
    "atomized_pack_list",
    html=(
        '<div class="pack-list" role="list" aria-label="Selected references"></div>'
        '<p class="sr-status" role="status" aria-live="polite"></p>'
    ),
    css=(ASSETS / "pack.css").read_text(encoding="utf-8"),
    js=(ASSETS / "pack.js").read_text(encoding="utf-8"),
)
