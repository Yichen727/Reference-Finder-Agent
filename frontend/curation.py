"""Keep pack drafts separate from Streamlit's transient widget state."""

import copy

EDIT_FIELDS = {
    "what_it_is": "What It Is",
    "what_to_borrow": "What to Borrow",
    "why_it_fits": "Why It Fits Your Brief",
    "production_note": "Production Considerations",
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
        "rejected_ids": list(selection.get("rejected_ids", [])),
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
        "rejected_ids": list(draft.get("rejected_ids", [])),
    }


def apply_pack_action(draft: dict, action: dict) -> bool:
    """Accept ID-based permutations or one explicit removal from the current revision."""
    if not isinstance(action, dict) or action.get("revision") != draft["revision"]:
        return False
    current = draft["ordered_ids"]
    order = action.get("ordered_ids")
    if not isinstance(order, list) or not all(isinstance(item, str) for item in order):
        return False
    if len(order) != len(set(order)):
        return False
    removed = action.get("removed_id")
    expected = set(current) - {removed} if removed in current else set(current)
    if set(order) != expected or (removed is not None and removed not in current):
        return False
    if order == current:
        return False
    draft["ordered_ids"] = list(order)
    return True
