"""Inline additional research and version controls; reuse the backend search pipeline."""

from uuid import uuid4

import streamlit as st

from config import current_source_name
from frontend.api_client import APIClient, APIError
from frontend.search_progress import pipeline_stage, show_search_progress

client = APIClient()


def restore_operation(run_id):
    operations = st.session_state.setdefault("research_operations", {})
    try:
        actions = client.request("GET", f"/search-runs/{run_id}/actions")
    except APIError:
        return
    active = next((item for item in actions if item["status"] in {"queued", "running"}), None)
    if active:
        operations[run_id] = active["id"]
        if run_id not in st.session_state.get("research_editors", {}):
            editor = editor_for(run_id)
            try:
                brief = client.request("GET", f"/briefs/{active['brief_id']}")
            except APIError:
                return
            mode = "Add" if active["operation"] == "add" else "Regenerate"
            editor.update(mode=mode, concept=brief["concept"], sources=brief["preferred_sources"])
            editor["add_count" if mode == "Add" else "regenerate_count"] = brief["requested_count"]


def is_busy(run_id):
    return bool(
        st.session_state.get("research_operations", {}).get(run_id)
        or st.session_state.get("research_action_requests", {}).get(run_id)
    )


def editor_for(run_id):
    editors = st.session_state.setdefault("research_editors", {})
    if run_id not in editors:
        brief = st.session_state["brief"]
        editors[run_id] = {
            "open": False,
            "mode": "Add",
            "concept": brief["concept"],
            "sources": list(dict.fromkeys(current_source_name(s) for s in brief["preferred_sources"])),
            "add_count": min(5, st.session_state["workspace_health"]["max_references"]),
            "regenerate_count": brief["requested_count"],
        }
    return editors[run_id]


def open_editor(run_id, mode):
    editor = editor_for(run_id)
    editor.update(open=True, mode=mode)
    st.session_state[f"research_mode_{run_id}"] = mode


def cancel_editor(run_id):
    st.session_state.get("research_editors", {}).pop(run_id, None)
    for key in list(st.session_state):
        if key.startswith(f"research_edit_{run_id}_") or key == f"research_mode_{run_id}":
            del st.session_state[key]


def retry_submission(run_id):
    st.session_state["research_action_requests"][run_id]["auto_submit"] = True


def submit_pending(run_id):
    pending = st.session_state.get("research_action_requests", {}).get(run_id)
    if not pending or not pending.pop("auto_submit", False):
        return
    try:
        with st.spinner("Submitting your research update..."):
            action = client.request("POST", f"/search-runs/{run_id}/actions", pending["payload"])
    except APIError as error:
        if error.status in {400, 401, 403, 404, 409, 422, 503}:
            # A definitive rejection created no job; allow correcting or cancelling the editor.
            st.session_state["research_action_requests"].pop(run_id, None)
            editor_for(run_id)["error"] = str(error)
            if error.code == "research_busy":
                restore_operation(run_id)
            return
        pending["error"] = str(error)
        return
    st.session_state.setdefault("research_operations", {})[run_id] = action["id"]
    st.session_state["research_action_requests"].pop(run_id, None)
    editor_for(run_id)["open"] = False
    st.rerun()


@st.fragment(run_every="2s")
def poll_operation(parent_id, task_id, activate_run):
    if st.session_state.get("run_id") != parent_id:
        return
    try:
        task = client.request("GET", f"/search-runs/{task_id}")
    except APIError as error:
        if st.session_state.get("run_id") == parent_id:
            st.error(f"Could not refresh the research update. Retrying automatically. {error}")
        return
    if st.session_state.get("run_id") != parent_id:
        return
    if task["status"] in {"queued", "running"}:
        count = len(task["strategy"]["preferred_sources"])
        index, message = pipeline_stage(
            "analysing_brief" if task.get("planning_pending") else task["stage"], source_count=count
        )
        show_search_progress(index, message)
        return
    st.session_state["research_operations"].pop(parent_id, None)
    if task["status"] in {"completed", "partial"}:
        target = parent_id if task["operation"] == "add" else task_id
        # Activate only after an atomic addition or a complete independent version exists.
        # If refreshing fails, keep the task ID so the next interaction can retry safely.
        try:
            activate_run(target)
        except APIError as error:
            st.session_state["research_operations"][parent_id] = task_id
            st.error(f"The search finished, but results could not be loaded. {error}")
            return
        editor_for(parent_id)["open"] = False
        st.session_state["research_action_notice"] = (
            f"Added {len(task['added_reference_ids'])} new references."
            if task["operation"] == "add"
            else "Opened the new research version."
        )
    else:
        message = {
            "no_additional_references": "No new works remained after removing existing references.",
            "no_search_evidence": (
                "No usable source evidence was found. Try different sources or a revised brief."
            ),
            "no_eligible_references": (
                "No new usable references were found. Try different sources or revise the brief."
            ),
        }.get(task.get("error_code"))
        if task["status"] == "interrupted":
            message = "The backend restarted before this update completed. Submit again to retry."
        editor_for(parent_id).update(
            open=True,
            error=message or (task.get("warnings") or ["The search failed. Please submit again."])[-1],
        )
    st.rerun(scope="app")


def render_editor(run, categories_loader, activate_run):
    run_id = run["id"]
    submit_pending(run_id)
    task_id = st.session_state.get("research_operations", {}).get(run_id)
    if task_id:
        poll_operation(run_id, task_id, activate_run)
        return
    editor = editor_for(run_id)
    if not editor["open"]:
        return
    pending = st.session_state.get("research_action_requests", {}).get(run_id)
    prefix = f"research_edit_{run_id}_"
    with st.container(border=True, key=f"research_editor_{run_id}", gap="small"):
        heading, modes = st.columns([3, 2], vertical_alignment="center")
        with modes:
            mode = st.radio(
                "Research mode", ["Add", "Regenerate"], key=f"research_mode_{run_id}",
                index=0 if editor["mode"] == "Add" else 1,
                horizontal=True, label_visibility="collapsed", disabled=bool(pending),
            )
        editor["mode"] = mode
        heading.markdown("#### Add References" if mode == "Add" else "#### Regenerate Research")
        editor["concept"] = st.text_area(
            "Creative Brief", value=editor["concept"], key=prefix + "concept",
            height=110, max_chars=6000, disabled=bool(pending),
        )
        try:
            options = [item["name"] for item in categories_loader()]
        except APIError:
            st.error("Source options are unavailable. Retry loading them; your edits are kept.")
            st.button("Retry Sources", key=prefix + "retry_sources")
            st.button("Cancel", key=prefix + "cancel", on_click=cancel_editor, args=(run_id,))
            return
        editor["sources"] = st.multiselect(
            "Sources", options, default=[s for s in editor["sources"] if s in options],
            key=prefix + "sources", disabled=bool(pending),
        )
        count_field = "add_count" if mode == "Add" else "regenerate_count"
        maximum = st.session_state["workspace_health"]["max_references"]
        editor[count_field] = int(st.number_input(
            "Additional References" if mode == "Add" else "Reference Count",
            min_value=1, max_value=maximum, value=min(editor[count_field], maximum),
            key=prefix + count_field, disabled=bool(pending),
        ))
        st.caption(
            "Existing cards, exclusions and Current Pack will be preserved."
            if mode == "Add" else
            "A new research version will be created. Your previous research and pack remain saved."
        )
        if editor.get("error"):
            st.error(
                f"The update did not complete. Existing research and your edits are kept. {editor['error']}"
            )
        if pending:
            st.error(
                f"Submission could not be confirmed. Retry safely with the same request. {pending['error']}"
            )
            st.button("Retry Submission", key=prefix + "retry", on_click=retry_submission, args=(run_id,))
        with st.container(horizontal=True, horizontal_alignment="right", gap="small"):
            st.button(
                "Cancel", key=prefix + "cancel", on_click=cancel_editor, args=(run_id,),
                disabled=bool(pending),
            )
            submitted = st.button(
                "Find More References" if mode == "Add" else "Regenerate Results",
                key=prefix + "submit", type="primary", disabled=bool(pending),
            )
        if submitted:
            if not editor["concept"].strip():
                st.error("Enter a Creative Brief.")
                return
            if not editor["sources"]:
                st.error("Select at least one source.")
                return
            editor.pop("error", None)
            st.session_state.setdefault("research_action_requests", {})[run_id] = {
                "payload": {
                    "action": "add" if mode == "Add" else "regenerate",
                    "brief": {
                        "concept": editor["concept"], "preferred_sources": editor["sources"],
                        "requested_count": editor[count_field],
                    },
                    "idempotency_key": str(uuid4()),
                },
                "auto_submit": True,
            }
            st.rerun()
