"""Display pipeline milestones without estimating elapsed time or remaining work."""

import re

import streamlit as st

STAGES = (
    "Analysing Creative Brief",
    "Selecting Sources",
    "Searching References",
    "Verifying & Ranking",
    "Preparing Results",
)


def pipeline_stage(stage: str, *, source_count: int | None = None) -> tuple[int | None, str]:
    selected = (
        ""
        if source_count is None
        else (f"{source_count} source {'category' if source_count == 1 else 'categories'} selected. ")
    )
    if stage == "analysing_brief":
        return 0, "Understanding your creative brief and constraints."
    if stage == "queued":
        return 1, selected + "Your brief is ready. Waiting for the search to start."
    if stage == "starting_external_search":
        return 1, selected + "Preparing search tasks for every selected source category."
    match = re.fullmatch(r"searching_(\d+)_of_(\d+)", stage)
    if match:
        return 2, selected + f"Searching task {match[1]} of {match[2]} for relevant creative work."
    messages = {
        "checking_public_pages": "Checking public source pages and supporting evidence.",
        "organizing_search_evidence": "Building creative analysis from the available source evidence.",
        "ranking_and_deduplicating": "Ranking relevant work and removing duplicate references.",
    }
    if stage in messages:
        return 3, messages[stage]
    return None, "Waiting for the latest search status."


def show_search_progress(index: int | None, message: str, *, area=None):
    """Fill a replaceable area; a stage only counts as complete after it has finished."""
    if area is None:
        area = st.empty()
    with area.container():
        status = st.status(STAGES[index] if index is not None else "Finding References", expanded=True)
        if index is not None:
            status.progress(index / len(STAGES), text=f"Stage {index + 1} of {len(STAGES)}")
        status.caption(message)
    return area
