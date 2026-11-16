"""Small, scoped layout rules for the native Streamlit workspace."""

import re

WORKSPACE_STYLE = """
<style>
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
