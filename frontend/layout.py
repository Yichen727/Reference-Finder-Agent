"""Small, scoped layout rules for the native Streamlit workspace."""

import re
from html import escape

WORKSPACE_STYLE = """
<style>
.stMainBlockContainer { padding: 4.5rem 2rem 4rem; }
.st-key-research_workspace { --rf-border: #dedbd6; }
.st-key-recent_research button { justify-content: flex-start; text-align: left; }
.st-key-recent_research button p { text-align: left !important; width: 100%; }
.st-key-recent_research button > div { width: 100%; justify-content: flex-start !important; }
.st-key-recent_research button[kind="primary"] {
  color: #25231f; background: #e8e5df; border-color: transparent;
}
.st-key-recent_research [data-testid="stColumn"] { min-width: 0 !important; }
.st-key-recent_research [data-testid="stPopoverButton"] { min-width: 28px; }
.st-key-recent_research [class*="st-key-manage_"] svg,
.st-key-recent_research [class*="st-key-manage_"] [data-testid="stIconMaterial"] { display: none; }
.st-key-recent_research [class*="st-key-manage_"] button p { text-align: center !important; }
[class*="st-key-card_actions_"] [data-testid="stColumn"] {
  min-width: 0 !important; flex: 1 1 0 !important;
}
[class*="st-key-card_actions_"] button,
[class*="st-key-card_actions_"] a {
  height: 2.5rem; min-height: 2.5rem; padding: .25rem .4rem;
}
[class*="st-key-card_actions_"] button p { line-height: 1.1; }
[class*="st-key-card_select_"] button p {
  position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0;
  overflow: hidden; clip-path: inset(50%); white-space: nowrap;
}
[class*="st-key-reference_card_"] {
  overflow: hidden; height: 100%;
}
[class*="st-key-reference_card_"][data-testid="stVerticalBlock"],
[class*="st-key-reference_card_"] [data-testid="stVerticalBlock"] { gap: .6rem; }
[class*="st-key-reference_card_"] h4 { font-size: 1.05rem; line-height: 1.35; padding-top: .2rem; }
.rf-preview {
  aspect-ratio: 16 / 9; width: 100%; overflow: hidden; border-radius: 7px;
  background: #eae8e3; display: flex; align-items: center; justify-content: center;
}
.rf-preview img { width: 100%; height: 100%; object-fit: contain; background: #111; }
[role="dialog"] .rf-preview { max-width: 24rem; margin-inline: auto; }
.rf-placeholder { color: #77736a; font: 13px sans-serif; text-align: center; }
.rf-placeholder span { display: block; margin-bottom: .4rem; font-size: 26px; opacity: .6; }
.st-key-pack_panel h3 { font-size: 1.2rem; }
.rf-pack-shortcut { display: none; }
@media (min-width: 1101px) {
  .st-key-research_workspace [data-testid="stColumn"]:has(.st-key-pack_panel) {
    position: sticky;
    top: 4rem;
    align-self: flex-start;
    max-height: calc(100dvh - 5rem);
    overflow-y: auto;
  }
}
@media (max-width: 1100px) {
  .st-key-research_workspace
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-pack_panel) {
    flex-direction: column;
  }
  .st-key-research_workspace
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-pack_panel)
  > [data-testid="stColumn"] {
    width: 100% !important;
    flex: 1 1 auto !important;
    min-width: 0 !important;
  }
  .rf-pack-shortcut { display: block; margin-bottom: 1rem; }
}
@media (max-width: 640px) {
  .stMainBlockContainer { padding: 4.5rem 1rem 3rem; }
}
</style>
"""


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
