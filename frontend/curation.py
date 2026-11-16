"""Keep editable pack drafts separate from Streamlit's transient widget state."""

import copy

EDIT_FIELDS = {
    "what_it_is": "What it is",
    "what_to_borrow": "What to Borrow",
    "why_it_fits": "Why it supports this idea",
    "production_note": "Production notes",
}


def draft_from_run(run: dict) -> dict:
    selection = run.get("selection") or {}
    overrides = selection.get("annotation_overrides", {})
    return {
        "ordered_ids": list(selection.get("ordered_ids", [])),
        "annotations": {
            item["id"]: copy.deepcopy(overrides.get(item["id"], item["annotation"]))
            for item in run["results"]
        },
        "expected_version": selection.get("version", 0),
        "rejection_notes": selection.get("rejection_notes", ""),
        "revision": 0,
    }


def selection_payload(draft: dict) -> dict:
    return {
        "ordered_ids": list(draft["ordered_ids"]),
        "annotation_overrides": {
            item_id: copy.deepcopy(draft["annotations"][item_id]) for item_id in draft["ordered_ids"]
        },
        "expected_version": draft["expected_version"],
        "rejection_notes": draft["rejection_notes"],
    }
