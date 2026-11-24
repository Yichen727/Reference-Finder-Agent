from base64 import b64encode
from importlib import reload
from pathlib import Path
from time import monotonic
from uuid import uuid4

import streamlit as st

from backend.services.annotation_text import borrow_points
from config import current_source_name
from frontend import layout, library
from frontend.access import require_access
from frontend.api_client import APIClient, APIError
from frontend.curation import EDIT_FIELDS, apply_pack_action, draft_from_run, selection_payload
from frontend.pack_component import pack_list
from frontend.preview_component import render_preview
from frontend.research_actions import is_busy, open_editor, render_editor, restore_operation
from frontend.search_progress import pipeline_stage, show_search_progress
from frontend.source_filters import filter_definitions, normalize_filter_values, reference_category

# A running Streamlit session may still hold layout from before this helper was added.
# Reload only this stateless module; keep research and pack drafts in session state.
if not hasattr(layout, "description_summary") or not hasattr(layout, "preview_images"):
    reload(layout)

client = APIClient()
st.set_page_config(page_title="Reference Finder", page_icon="A", layout="wide")


def draft_for(run: dict) -> dict:
    drafts = st.session_state.setdefault("pack_drafts", {})
    if run["id"] not in drafts:
        drafts[run["id"]] = draft_from_run(run)
    else:
        for item_id, annotation in draft_from_run(run)["annotations"].items():
            drafts[run["id"]]["annotations"].setdefault(item_id, annotation)
    return drafts[run["id"]]


def changed(run_id: str):
    st.session_state["pack_drafts"][run_id]["revision"] += 1
    st.session_state.setdefault("pack_downloads", {}).pop(run_id, None)


def select_reference(run_id: str, item_id: str, key: str):
    order = st.session_state["pack_drafts"][run_id]["ordered_ids"]
    if st.session_state[key] and item_id not in order:
        if len(order) >= st.session_state["workspace_health"]["max_references"]:
            st.session_state[key] = False
            return
        order.append(item_id)
        rejected = st.session_state["pack_drafts"][run_id].setdefault("rejected_ids", [])
        if item_id in rejected:
            rejected.remove(item_id)
    elif not st.session_state[key] and item_id in order:
        order.remove(item_id)
    changed(run_id)


def remove_reference(run_id: str, item_id: str):
    st.session_state["pack_drafts"][run_id]["ordered_ids"].remove(item_id)
    st.session_state[f"pick_{run_id}_{item_id}"] = False
    changed(run_id)


def toggle_reference(run_id: str, item_id: str):
    if library.enabled():
        library.toggle_pack(run_id, item_id)
        return
    key = f"pick_{run_id}_{item_id}"
    order = st.session_state["pack_drafts"][run_id]["ordered_ids"]
    st.session_state[key] = item_id not in order
    select_reference(run_id, item_id, key)


def pack_action(run_id: str, key: str):
    action = st.session_state.get(key, {}).get("action")
    draft = st.session_state["pack_drafts"][run_id]
    before = set(draft["ordered_ids"])
    if apply_pack_action(draft, action):
        for item_id in before - set(draft["ordered_ids"]):
            st.session_state[f"pick_{run_id}_{item_id}"] = False
        changed(run_id)


def exclude_reference(run_id: str, item_id: str):
    if library.enabled() and library.is_selected(run_id, item_id):
        library.toggle_pack(run_id, item_id)
    draft = st.session_state["pack_drafts"][run_id]
    rejected = draft.setdefault("rejected_ids", [])
    if item_id not in rejected:
        rejected.append(item_id)
    if item_id in draft["ordered_ids"]:
        draft["ordered_ids"].remove(item_id)
    st.session_state[f"pick_{run_id}_{item_id}"] = False
    changed(run_id)


def restore_excluded(run_id: str):
    st.session_state["pack_drafts"][run_id]["rejected_ids"] = []
    changed(run_id)


def clear_filters(run_id: str):
    st.session_state[f"source_filter_{run_id}"] = []
    st.session_state.pop(f"strand_filter_{run_id}", None)


def close_detail():
    st.session_state.pop("active_detail", None)


def close_management():
    st.session_state.pop("research_management", None)


def open_management(action: str, run_id: str, title: str):
    close_detail()
    st.session_state["research_management"] = {"action": action, "run_id": run_id, "title": title}
    if action == "rename":
        st.session_state[f"rename_title_{run_id}"] = title


def open_detail(run_id: str, item_id: str):
    st.session_state["active_detail"] = (run_id, item_id)


def exclude_from_detail(run_id: str, item_id: str):
    exclude_reference(run_id, item_id)
    close_detail()


def activate_run(run_id: str, *, reload_saved=False):
    run = client.request("GET", f"/search-runs/{run_id}")
    brief = client.request("GET", f"/briefs/{run['brief_id']}")
    if reload_saved:
        st.session_state.setdefault("pack_drafts", {})[run_id] = draft_from_run(run)
        for key in list(st.session_state):
            if key.startswith((f"pick_{run_id}_", f"edit_{run_id}_")):
                del st.session_state[key]
        st.session_state.setdefault("pack_downloads", {}).pop(run_id, None)
        st.session_state.pop("conflict_run", None)
    st.session_state.update(run_id=run_id, run=run, brief=brief)
    st.session_state["view"] = "research"
    st.query_params.pop("view", None)
    library.close_management()
    restore_operation(run_id)
    st.session_state["recent_runs"] = client.request("GET", "/search-runs")
    close_detail()
    close_management()
    st.session_state.pop("pending_research", None)
    st.query_params["run"] = run_id


def new_research():
    st.session_state["view"] = "research"
    library.close_management()
    close_detail()
    close_management()
    for key in ["run_id", "run", "brief", "pending_research", "last_error"]:
        st.session_state.pop(key, None)
    st.query_params.clear()
    st.session_state.pop("workspace_health", None)
    st.session_state.pop("recent_runs", None)


def show_favorites():
    new_research()
    st.session_state["view"] = "favorites"
    st.query_params["view"] = "favorites"
    library.refresh()


def display_error(error: APIError):
    st.session_state["last_error"] = str(error)
    if error.code in {
        "export_template_missing",
        "export_template_invalid",
        "export_text_overflow",
        "export_text_invalid",
        "pptx_backend_unavailable",
    }:
        st.error(str(error).split(" (")[0])
    elif error.code == "selection_conflict":
        st.session_state["conflict_run"] = st.session_state.get("run_id")
        st.error("This saved pack changed in another session. Load the saved version before exporting.")
    elif error.code in {"openai_not_configured", "openai_authentication", "openai_permission"}:
        st.error("The search service is unavailable. Please contact the workspace administrator.")
    elif error.code in {"openai_rate_limit", "openai_quota"}:
        st.error("The search service has reached its usage limit. Please try again later.")
    elif error.code == "validation_error":
        st.error("Please check your entries and try again.")
    elif error.status == 405:
        st.error(
            "This action requires the latest backend. Restart the workspace, then try again. "
            "Your current edits are kept until you restart. Export your pack first to save them."
        )
    elif error.status == 409:
        st.error(str(error).split(" (")[0])
    else:
        st.error("We could not complete that request. Please try again. Your current edits are kept.")


def toggle_recent_research():
    collapsed = st.session_state.get("recent_research_collapsed", False)
    st.session_state["recent_research_collapsed"] = not collapsed


def sidebar():
    with st.sidebar:
        logo = Path(__file__).with_name("assets") / "atomized-studios-logo.png"
        encoded_logo = b64encode(logo.read_bytes()).decode("ascii")
        st.html(f'<img class="rf-sidebar-logo" src="data:image/png;base64,{encoded_logo}" '
                'alt="Atomized Studios" />')
        with st.container(key="sidebar_navigation", gap="xxsmall"):
            st.button(
                "New Research", icon=":material/edit_square:", key="sidebar_new_research",
                icon_position="left", on_click=new_research, width="stretch",
                type="primary" if not st.session_state.get("run_id")
                and st.session_state.get("view") != "favorites" else "tertiary",
            )
            if library.enabled():
                favorite_count = len(library.state()["favorites"])
                st.button(
                    f"Favorites ({favorite_count})", icon=":material/bookmark:", key="open_favorites",
                    icon_position="left", on_click=show_favorites, width="stretch",
                    type="primary" if st.session_state.get("view") == "favorites" else "tertiary",
                )
        if "recent_runs" not in st.session_state:
            st.session_state["recent_runs"] = client.request("GET", "/search-runs")
        recent = st.session_state["recent_runs"]
        recent = [item for item in recent if item.get("mode") == "live"]
        collapsed = st.session_state.get("recent_research_collapsed", False)
        with st.container(key="recent_research_heading"):
            st.button(
                "RECENT RESEARCH",
                icon=":material/chevron_right:" if collapsed else ":material/expand_more:",
                icon_position="right", key="toggle_recent_research", type="tertiary", width="content",
                on_click=toggle_recent_research,
            )
        if recent and not collapsed:
            with st.container(key="recent_research", gap="xxsmall"):
                for item in recent:
                    active = item["id"] == st.session_state.get("run_id")
                    title = item.get("title") or item["summary"][:48]
                    with st.container(key=f"recent_row_{item['id']}"):
                        title_column, menu_column = st.columns(
                            [5, 1], gap="xxsmall", wrap=False, vertical_alignment="center",
                        )
                        if title_column.button(
                            title,
                            key=f"research_{item['id']}",
                            width="stretch", wrap=False,
                            type="primary" if active else "tertiary",
                        ):
                            activate_run(item["id"])
                            st.rerun()
                        with menu_column.popover(
                            "⋯", key=f"manage_{item['id']}",
                            type="tertiary", width="stretch",
                        ):
                            with st.container(key=f"management_menu_research_{item['id']}", gap="xxsmall"):
                                st.button(
                                    "Rename Research", key=f"rename_{item['id']}", width="stretch",
                                    icon=":material/edit:", icon_position="left", type="tertiary",
                                    on_click=open_management, args=("rename", item["id"], title),
                                )
                                st.divider()
                                st.button(
                                    "Delete Research", key=f"delete_{item['id']}", width="stretch",
                                    icon=":material/delete:", icon_position="left", type="tertiary",
                                    on_click=open_management, args=("delete", item["id"], title),
                                )
        elif not collapsed:
            st.caption("No saved research yet.")


@st.dialog("Manage Research", width="small", on_dismiss=close_management)
def management_dialog():
    target = st.session_state["research_management"]
    run_id = target["run_id"]
    if target["action"] == "rename":
        st.subheader("Rename Research")
        title = st.text_input("Research title", key=f"rename_title_{run_id}", max_chars=100)
        save, cancel = st.columns(2)
        submitted = save.button("Save Title", type="primary", width="stretch")
        cancelled = cancel.button("Cancel", width="stretch")
        if submitted:
            if not title.strip():
                st.error("Enter a research title.")
                return
            try:
                saved = client.request("PATCH", f"/search-runs/{run_id}/title", {"title": title.strip()})
            except APIError as error:
                display_error(error)
                return
            for item in st.session_state.get("recent_runs", []):
                if item["id"] == run_id:
                    item["title"] = saved["title"]
            presentation = st.session_state.get("presentations", {}).get(run_id)
            if presentation is not None:
                presentation["title"] = saved["title"]
            close_management()
            st.rerun()
    else:
        st.subheader("Delete Research?")
        st.write(target["title"])
        st.warning(
            "This research will disappear from Recent Research and its saved links will stop opening it."
        )
        st.caption("The saved pack and export files are retained safely. Other research is unaffected.")
        remove, cancel = st.columns(2)
        submitted = remove.button(
            "Delete Research", key="confirm_research_delete", type="primary", width="stretch",
        )
        cancelled = cancel.button("Cancel", width="stretch")
        if submitted:
            try:
                client.request("DELETE", f"/search-runs/{run_id}")
            except APIError as error:
                display_error(error)
                return
            recent = [item for item in st.session_state.get("recent_runs", []) if item["id"] != run_id]
            for cache in ["pack_drafts", "pack_downloads", "presentations"]:
                st.session_state.get(cache, {}).pop(run_id, None)
            if st.session_state.get("conflict_run") == run_id:
                st.session_state.pop("conflict_run", None)
            if st.session_state.get("run_id") == run_id:
                new_research()
            st.session_state["recent_runs"] = recent
            close_management()
            st.rerun()
    if cancelled:
        close_management()
        st.rerun()


def submit_pending():
    pending = st.session_state["pending_research"]
    area = st.empty()
    try:
        if pending.get("background_planning"):
            show_search_progress(None, "Submitting research to the background search queue.", area=area)
            run = client.request("POST", "/search-runs", {
                "brief": pending["brief_input"], "idempotency_key": pending["submission_key"],
            })
            activate_run(run["id"])
            st.rerun()
        if pending.get("strategy") is None:
            show_search_progress(0, "Understanding your idea and creative constraints.", area=area)
            if pending.get("brief") is None:
                pending["brief"] = client.request("POST", "/briefs", pending["brief_input"])
            pending["strategy"] = client.request("POST", f"/briefs/{pending['brief']['id']}/strategy")
        count = len({current_source_name(name) for name in pending["strategy"]["preferred_sources"]})
        show_search_progress(
            1,
            f"{count} source {'category' if count == 1 else 'categories'} selected. "
            "Preparing at least one search for each selected category.",
            area=area,
        )
        run = client.request(
            "POST",
            "/search-runs",
            {
                "brief_id": pending["brief"]["id"],
                "strategy": pending["strategy"],
                "idempotency_key": pending["submission_key"],
            },
        )
        activate_run(run["id"])
    finally:
        area.empty()
    st.rerun()


def source_categories():
    if "source_categories" not in st.session_state or st.session_state.get("source_names_version") != 2:
        categories = client.request("GET", "/source-categories")
        if (
            not isinstance(categories, list)
            or len(categories) != 8
            or any(
                not isinstance(category, dict)
                or not isinstance(category.get("name"), str)
                or not category["name"].strip()
                or not isinstance(category.get("default_selected"), bool)
                for category in categories
            )
            or len({category["name"] for category in categories}) != len(categories)
        ):
            raise APIError("The source category list is unavailable.")
        st.session_state["source_categories"] = categories
        # Refresh old cached labels once, retaining explicit deselections and drafts.
        for key in ("brief_sources", "research_source_choices"):
            if key in st.session_state:
                st.session_state[key] = list(
                    dict.fromkeys(current_source_name(name) for name in st.session_state[key])
                )
        st.session_state["source_names_version"] = 2
    return st.session_state["source_categories"]


def brief_form(health):
    previous_constraints = st.session_state.pop("brief_constraints", "").strip()
    if previous_constraints:
        current = st.session_state.get("brief_concept", "")
        if previous_constraints not in current:
            st.session_state["brief_concept"] = (
                f"{current.rstrip()}\n\nConstraints: {previous_constraints}".strip()
            )
    try:
        categories = source_categories()
    except APIError:
        categories = None
        st.error("Source options are temporarily unavailable. Retry loading them to start your search.")
    with st.form("brief_form"):
        concept = st.text_area(
            "Describe your creative idea",
            key="brief_concept",
            height=180,
            placeholder=(
                "Describe your idea, audience, format and tone. Include any budget, timing, "
                "required elements or things to avoid."
            ),
        )
        if categories is not None:
            options = [category["name"] for category in categories]
            choices = st.session_state.get(
                "research_source_choices",
                [category["name"] for category in categories if category["default_selected"]],
            )
            if "brief_sources" not in st.session_state:
                st.session_state["brief_sources"] = [choice for choice in choices if choice in options]
            sources = st.multiselect("Sources", options, key="brief_sources")
        else:
            sources = []
            st.caption("Sources will appear once the options have loaded.")
        count = st.number_input(
            "Reference Count", 1, health["max_references"], min(10, health["max_references"])
        )
        submit = st.form_submit_button("Find References", type="primary", disabled=categories is None)
        if categories is None:
            # Submit the form to preserve its pending inputs while retrying metadata.
            st.form_submit_button("Retry Sources", key="retry_source_categories")
    if categories is not None:
        # Keep source preferences outside widget state so navigation does not reset them.
        st.session_state["research_source_choices"] = list(sources)
    if submit:
        if not sources:
            st.error("Select at least one source.")
            return
        if not concept.strip():
            st.error("Describe your creative idea.")
            return
        if not health["live_search_ready"]:
            st.error("The search service is unavailable. Please contact the workspace administrator.")
            return
        # Persist the request before any network call and replace the submit form.
        # Repeated click events cannot create a second submission or a new retry key.
        st.session_state["pending_research"] = {
            "brief_input": {
                "concept": concept,
                "preferred_sources": sources,
                "requested_count": int(count),
            },
            "brief": None,
            "strategy": None,
            "submission_key": str(uuid4()),
            "background_planning": bool(health.get("capabilities", {}).get("background_planning")),
            "auto_start": True,
        }
        close_detail()
        st.rerun()


def resume_search():
    if st.session_state["pending_research"].pop("auto_start", False):
        submit_pending()
        return
    st.info("Your idea is saved. Continue your search without submitting it again.")
    if st.button("Continue Search", type="primary"):
        submit_pending()


@st.fragment(run_every="2s")
def poll_run(run_id):
    if st.session_state.get("run_id") != run_id:
        return
    try:
        run = client.request("GET", f"/search-runs/{run_id}")
        if st.session_state.get("run_id") != run_id:
            return
        st.session_state["run"] = run
        if run["status"] not in {"queued", "running"}:
            st.rerun()
        count = len({current_source_name(name) for name in run["strategy"]["preferred_sources"]})
        index, message = pipeline_stage(
            "analysing_brief" if run.get("planning_pending") else run["stage"], source_count=count
        )
        show_search_progress(index, message)
    except APIError as error:
        if st.session_state.get("run_id") == run_id:
            display_error(error)


def presentation_needs_refresh(run):
    cached = st.session_state.get("presentations", {}).get(run["id"])
    if cached is None or st.session_state.get("presentation_cache_version") != 7:
        return True
    if st.session_state.get("presentation_result_ids", {}).get(run["id"]) != [
        item["id"] for item in run["results"]
    ]:
        return True
    missing = any(
        not (cached.get("previews", {}).get(item["id"]) or {}).get("image") for item in run["results"]
    )
    loaded_at = st.session_state.get("presentation_loaded_at", {}).get(run["id"], 0)
    return missing and monotonic() - loaded_at >= 120


def presentation_for(run):
    presentations = st.session_state.setdefault("presentations", {})
    if st.session_state.get("presentation_cache_version") != 7:
        presentations.clear()
        st.session_state["presentation_cache_version"] = 7
    loaded_at = st.session_state.setdefault("presentation_loaded_at", {})
    cached = presentations.get(run["id"])
    if presentation_needs_refresh(run):
        try:
            presentations[run["id"]] = client.request("GET", f"/search-runs/{run['id']}/presentation")
            st.session_state.setdefault("presentation_result_ids", {})[run["id"]] = [
                item["id"] for item in run["results"]
            ]
            loaded_at[run["id"]] = monotonic()
        except APIError:
            # Preview enrichment never prevents browsing an existing result or exporting it.
            # Do not persist a request failure as a permanent empty presentation.
            return cached or {"previews": {}}
    return presentations[run["id"]]


def reference_card(run, item, draft, previews):
    run_id, item_id = run["id"], item["id"]
    with st.container(border=True, key=f"reference_card_{item_id}"):
        if library.enabled():
            favorite = library.is_favorite(run_id, item_id)
            with st.container(key=f"card_bookmark_{item_id}"):
                st.button(
                    "Remove from Favorites" if favorite else "Add to Favorites",
                    icon=":material/bookmark:" if favorite else ":material/bookmark_add:",
                    type="primary" if favorite else "secondary", width=36,
                    key=f"favorite_{run_id}_{item_id}", disabled=library.blocked(),
                    help="Remove from Favorites" if favorite else "Add to Favorites",
                    on_click=library.toggle_favorite, args=(run_id, item_id),
                )
        render_preview(previews.get(item_id), item["title"], key=f"card_preview_{run_id}_{item_id}")
        st.markdown(f"#### {item['title']}")
        st.caption(f"{item['platform']} / {item['strand']}")
        annotation = draft["annotations"][item_id]
        description = annotation.get("what_it_is") or (item.get("annotation") or {}).get("what_it_is")
        st.markdown(layout.description_summary(description))
        with st.container(key=f"card_actions_{item_id}"):
            details, link, selection = st.columns([6, 3, 1.2], gap="xsmall", wrap=False)
            details.button(
                "View Details",
                key=f"details_{item_id}",
                width="stretch",
                wrap=False,
                help=f"Read the existing analysis for {item['title']}",
                on_click=open_detail,
                args=(run_id, item_id),
            )
            link.link_button(
                "Link",
                item["source_url"],
                type="tertiary",
                icon=":material/open_in_new:",
                icon_position="right",
                width="stretch",
                help="Open the original source in a new tab",
            )
            selected = (library.is_selected(run_id, item_id)
                        if library.enabled() else item_id in draft["ordered_ids"])
            with selection.container(key=f"card_select_{item_id}"):
                st.button(
                    "Remove from Pack" if selected else "Add to Pack",
                    icon=":material/check:" if selected else ":material/add:",
                    key=f"pick_action_{run_id}_{item_id}",
                    width=40,
                    type="primary" if selected else "secondary",
                    disabled=(library.blocked() or (not selected and library.pack_full()))
                    if library.enabled() else not selected and len(draft["ordered_ids"]) >= st.session_state[
                        "workspace_health"]["max_references"],
                    help="Remove from Pack" if selected else "Add to Pack",
                    on_click=toggle_reference,
                    args=(run_id, item_id),
                )


@st.dialog("Reference Details", width="medium", on_dismiss=close_detail)
def reference_details(run, item):
    run_id, item_id = run["id"], item["id"]
    annotation = draft_for(run)["annotations"][item_id]
    preview = st.session_state.get("presentations", {}).get(run_id, {}).get("previews", {}).get(item_id)
    st.subheader(item["title"])
    st.caption(f"{item['platform']} / {item['strand']}")
    st.caption(f"Creator: {item.get('creator') or 'Not listed'}")
    render_preview(preview, item["title"], key=f"detail_preview_{run_id}_{item_id}", detail=True)
    original, selection = st.columns(2)
    original.link_button("Open Link", item["source_url"], width="stretch")
    selected = (library.is_selected(run_id, item_id)
                if library.enabled() else item_id in draft_for(run)["ordered_ids"])
    if selection.button(
        "Remove from Pack" if selected else "Add to Pack",
        key=f"detail_select_{item_id}",
        width="stretch",
        disabled=(library.blocked() or (not selected and library.pack_full()))
        if library.enabled() else not selected and len(draft_for(run)["ordered_ids"]) >= st.session_state[
            "workspace_health"]["max_references"],
        on_click=toggle_reference,
        args=(run_id, item_id),
    ):
        st.rerun()
    for field in ("what_it_is", "what_to_borrow", "why_it_fits"):
        st.markdown(f"**{EDIT_FIELDS[field]}**")
        if field == "what_to_borrow":
            st.markdown("\n".join(f"- {point}" for point in borrow_points(annotation[field]))
                        or "Not provided.")
        else:
            st.write(annotation[field])
    with st.expander("Source & Rights", expanded=False):
        st.markdown("**Rights and usage**")
        st.write(annotation["rights_note"])
        if preview and preview.get("image"):
            st.caption(
                (preview.get("basis") or "Source page preview")
                + ". Preview use does not establish video or image reproduction rights."
            )
            if preview.get("source_url"):
                st.link_button("Thumbnail Source", preview["source_url"])
        elif preview and preview.get("reason"):
            st.caption("Preview unavailable: " + preview["reason"])
        st.caption(
            "Source descriptions report evidence; borrowing ideas and brief-fit analysis "
            "are creative interpretations."
        )
        check = item.get("page_check")
        if check:
            st.write(f"Source page: {check['status']} / {check['reason']}")
            if check.get("final_url") and check["final_url"] != item["source_url"]:
                st.link_button("Checked Destination", check["final_url"], key=f"checked_{item_id}")
        st.caption(f"Evidence: {item['verification_status']} / Confidence: {annotation['confidence']}")
        for evidence in run["evidence"]:
            if evidence["id"] in item["evidence_ids"]:
                st.caption(f"{evidence['evidence_type']} / {evidence['retrieved_at']}")
                st.text(evidence["excerpt"])
                st.link_button(
                    "Evidence Source", evidence["source_url"], key=f"source_{item_id}_{evidence['id']}"
                )
        for index, url in enumerate(item.get("alternate_urls", [])):
            st.link_button("Alternate Source", url, key=f"alternate_{item_id}_{index}")
    done, exclude = st.columns(2)
    if done.button("Done", type="primary", width="stretch"):
        close_detail()
        st.rerun()
    if run_id != "favorites" and exclude.button(
        "Exclude Reference",
        key=f"exclude_{item_id}",
        width="stretch",
        on_click=exclude_from_detail,
        args=(run_id, item_id),
    ):
        st.rerun()


def export_pack(run: dict, draft: dict):
    saved = client.request("PUT", f"/search-runs/{run['id']}/selection", selection_payload(draft))
    # Advance the local version even if document generation or download subsequently fails.
    draft["expected_version"] = saved["version"]
    run["selection"] = saved
    try:
        record = client.request(
            "POST", f"/search-runs/{run['id']}/exports/pptx", {"expected_selection_version": saved["version"]}
        )
    except APIError as error:
        if error.status in {404, 405} and error.code == "http_error":
            raise APIError(
                "Your pack has been saved. Restart the workspace to load the PowerPoint exporter, "
                "then retry.",
                code="pptx_backend_unavailable",
            ) from None
        raise
    if record.get("format") != "pptx":
        raise APIError("The server did not return a PowerPoint export. Restart the workspace and retry.")
    if record["selection_version"] != saved["version"]:
        raise APIError("The saved pack changed before export.", code="selection_conflict", status=409)
    content = client.request("GET", f"/exports/{record['id']}/download", binary=True)
    st.session_state.setdefault("pack_downloads", {})[run["id"]] = {
        "content": content,
        "version": saved["version"],
        "revision": draft["revision"],
        "format": "pptx",
    }


def selection_panel(run, draft):
    if library.enabled():
        library.render_current_pack()
        return
    st.subheader(f"Current Pack ({len(draft['ordered_ids'])})", anchor="your-pack")
    by_id = {item["id"]: item for item in run["results"]}
    st.caption("Drag a row to reorder")
    key = f"pack_sort_{run['id']}"
    pack_list(
        data={
            "items": [{"id": item_id, "title": by_id[item_id]["title"]} for item_id in draft["ordered_ids"]],
            "revision": draft["revision"],
        },
        key=key,
        on_action_change=lambda: pack_action(run["id"], key),
    )
    if not draft["ordered_ids"]:
        st.caption("Add references to build your pack.")
    if st.session_state.get("conflict_run") == run["id"]:
        st.info("Load the saved pack to resolve the version conflict. This replaces your local edits.")
        if st.button("Load Saved Pack"):
            activate_run(run["id"], reload_saved=True)
            st.rerun()
    download = st.session_state.setdefault("pack_downloads", {}).get(run["id"])
    if download and download.get("format") != "pptx":
        download = None
    if st.button(
        "Export Reference Pack",
        type="secondary" if download else "primary",
        disabled=not draft["ordered_ids"],
        width="stretch",
    ):
        with st.spinner("Preparing your document..."):
            export_pack(run, draft)
        st.rerun()
    if download and download["revision"] == draft["revision"]:
        st.download_button(
            "Download Reference Pack (.pptx)",
            download["content"],
            type="primary",
            file_name=f"Atomized-Reference-Pack-{run['id'][:8]}.pptx",
            mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            width="stretch",
        )


def result_filters(run, draft, active_filters, categories):
    with st.popover(
        str(active_filters) if active_filters else "",
        icon=":material/tune:", type="tertiary", key=f"result_filters_{run['id']}",
        width=80 if active_filters else 48,
        help=(f"Filter references ({active_filters} selected values)"
              if active_filters else "Filter references"),
    ):
        source_filter = st.multiselect("Sources", categories, key=f"source_filter_{run['id']}")
        st.button(
            "Clear Filters", key=f"clear_filters_{run['id']}", disabled=not active_filters,
            on_click=clear_filters, args=(run["id"],), width="stretch",
        )
        if draft.get("rejected_ids"):
            st.divider()
            st.caption(f"{len(draft['rejected_ids'])} references excluded.")
            st.button("Restore Excluded References", on_click=restore_excluded, args=(run["id"],))
    return source_filter


def result_panel():
    run = st.session_state["run"]
    if library.enabled():
        library.ensure(run)
    if run.get("mode") != "live":
        st.info("This archived demonstration is read-only. Start new research to find current references.")
        return
    if run["status"] in {"queued", "running"}:
        poll_run(run["id"])
        return
    if run["status"] not in {"completed", "partial"}:
        if run.get("planning_pending") and run.get("warnings"):
            st.error(f"We could not finish analysing your brief. {run['warnings'][-1]}")
        elif run.get("error_code") in {"no_eligible_references", "no_search_evidence"}:
            st.error(
                "No usable references remained after source checks. Try a different idea or broader sources."
            )
        else:
            st.error("We could not complete this research. Please try a new search.")
        return
    draft = draft_for(run)
    definitions = filter_definitions(st.session_state.get("source_categories"))
    categories_by_id = {item["id"]: reference_category(item, definitions) for item in run["results"]}
    filter_options = [item["name"] for item in definitions if item["name"] in categories_by_id.values()]
    filter_key = f"source_filter_{run['id']}"
    if filter_key in st.session_state:
        normalized = normalize_filter_values(
            st.session_state[filter_key], run["results"], categories_by_id, filter_options,
        )
        if normalized != st.session_state[filter_key]:
            st.session_state[filter_key] = normalized
    area = None
    if presentation_needs_refresh(run):
        area = show_search_progress(4, "Loading reference cards and available previews.")
    try:
        presentation = presentation_for(run)
    finally:
        if area is not None:
            area.empty()
    st.html('<a class="rf-pack-shortcut" href="#your-pack">View Current Pack</a>')
    with st.container(key="research_workspace"):
        results, pack = st.columns([7, 3], gap="medium")
        with results, st.container(key="reference_results", gap="xsmall"):
            with st.container(key="results_summary", gap="xxsmall"):
                active_filters = len(st.session_state.get(f"source_filter_{run['id']}", []))
                with st.container(
                    key=f"results_heading_{run['id']}",
                    horizontal=True,
                    wrap=True,
                    gap="small",
                    horizontal_alignment="distribute",
                    vertical_alignment="center",
                ):
                    st.subheader("Reference Results", width="content")
                    with st.container(
                        key=f"results_actions_{run['id']}", horizontal=True, width="content",
                        gap="xsmall", vertical_alignment="center",
                    ):
                        source_filter = result_filters(run, draft, active_filters, filter_options)
                        st.button(
                            "Add References", icon=":material/add:", type="primary",
                            key=f"add_references_{run['id']}", disabled=is_busy(run["id"]),
                            on_click=open_editor, args=(run["id"], "Add"),
                        )
                        st.button(
                            "Regenerate", icon=":material/refresh:",
                            key=f"regenerate_references_{run['id']}", disabled=is_busy(run["id"]),
                            on_click=open_editor, args=(run["id"], "Regenerate"),
                        )
                visible = [
                    item
                    for item in run["results"]
                    if item["id"] not in draft.get("rejected_ids", [])
                    and (not source_filter or categories_by_id[item["id"]] in source_filter)
                ]
                title = presentation.get("title") or next(
                    (
                        item.get("title")
                        for item in st.session_state.get("recent_runs", [])
                        if item["id"] == run["id"] and item.get("title")
                    ),
                    "Saved Research",
                )
                st.caption(f"{len(visible)} Results · {title}")
            notice = st.session_state.pop("research_action_notice", None)
            if notice:
                st.toast(notice)
            render_editor(run, source_categories, activate_run)
            if not visible:
                st.info(
                    "No references match these filters. Adjust the filters or restore excluded references."
                )
            reference_grid(run, visible, draft, presentation["previews"])
        with pack:
            with st.container(key="pack_panel", border=True):
                selection_panel(run, draft)


def reference_grid(run, items, draft, previews):
    for start in range(0, len(items), 2):
        with st.container(key=f"reference_grid_{start}"):
            columns = st.columns(2, gap="small")
            for column, item in zip(columns, items[start:start + 2]):
                with column:
                    reference_card(run, item, draft, previews)


def favorites_panel():
    run = library.favorites_context()
    draft = draft_for(run)
    previews = library.favorites_previews()
    st.session_state.setdefault("presentations", {})["favorites"] = {"previews": previews}
    with st.container(key="research_workspace"):
        results, pack = st.columns([7, 3], gap="medium")
        with results:
            st.subheader("Favorites")
            st.caption(f"{len(run['results'])} saved references")
            if not run["results"]:
                st.caption("Bookmark a reference from any research to save it here.")
            reference_grid(run, run["results"], draft, previews)
        with pack, st.container(key="pack_panel", border=True):
            selection_panel(run, draft)


def main():
    require_access()
    stylesheet = Path(__file__).with_name("workspace.css").read_text(encoding="utf-8")
    st.html(f"<style>{stylesheet}</style>")
    st.title("Atomized Reference Finder")
    st.write("Turn your creative idea into a curated reference pack.")
    try:
        if "workspace_health" not in st.session_state:
            st.session_state["workspace_health"] = client.request("GET", "/health")
        health = st.session_state["workspace_health"]
        if "mode" not in health or "max_references" not in health:
            raise APIError("This service is not the Reference Finder backend.")
        if st.query_params.get("view") == "favorites" and library.enabled():
            st.session_state["view"] = "favorites"
        if not st.session_state.get("run_id") and st.query_params.get("run"):
            try:
                activate_run(st.query_params["run"])
            except APIError:
                st.query_params.clear()
                st.warning("That saved research is unavailable. Start a new search below.")
        sidebar()
        if st.session_state.get("view") == "favorites" and library.enabled():
            favorites_panel()
        elif st.session_state.get("pending_research"):
            resume_search()
        elif st.session_state.get("run"):
            result_panel()
        else:
            brief_form(health)
        detail = st.session_state.get("active_detail")
        run = st.session_state.get("run")
        if st.session_state.get("view") == "favorites" and library.enabled():
            run = library.favorites_context()
        if st.session_state.get("pack_management"):
            library.management_dialog()
        elif st.session_state.get("research_management"):
            management_dialog()
        elif detail and run and detail[0] == run["id"]:
            item = next((item for item in run["results"] if item["id"] == detail[1]), None)
            if item:
                reference_details(run, item)
    except APIError as error:
        display_error(error)
        if st.button("Retry"):
            st.rerun()


main()
