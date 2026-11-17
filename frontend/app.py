from pathlib import Path
from uuid import uuid4

import streamlit as st

from frontend.api_client import APIClient, APIError
from frontend.curation import EDIT_FIELDS, apply_pack_action, draft_from_run, selection_payload
from frontend.layout import borrow_summary, preview_markup
from frontend.pack_component import pack_list

SOURCES = [
    "Vimeo",
    "YouTube",
    "Instagram",
    "Pinterest",
    "Studio portfolios",
    "Production companies",
    "Award archives",
]
client = APIClient()
st.set_page_config(page_title="Reference Finder", page_icon="A", layout="wide")


def draft_for(run: dict) -> dict:
    drafts = st.session_state.setdefault("pack_drafts", {})
    if run["id"] not in drafts:
        drafts[run["id"]] = draft_from_run(run)
    return drafts[run["id"]]


def changed(run_id: str):
    st.session_state["pack_drafts"][run_id]["revision"] += 1
    st.session_state.setdefault("pack_downloads", {}).pop(run_id, None)


def select_reference(run_id: str, item_id: str, key: str):
    order = st.session_state["pack_drafts"][run_id]["ordered_ids"]
    if st.session_state[key] and item_id not in order:
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
    st.session_state[f"strand_filter_{run_id}"] = []


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
    st.session_state["recent_runs"] = client.request("GET", "/search-runs")
    close_detail()
    close_management()
    st.session_state.pop("pending_research", None)
    st.query_params["run"] = run_id


def new_research():
    close_detail()
    close_management()
    for key in ["run_id", "run", "brief", "pending_research", "last_error"]:
        st.session_state.pop(key, None)
    st.query_params.clear()
    st.session_state.pop("workspace_health", None)
    st.session_state.pop("recent_runs", None)


def display_error(error: APIError):
    st.session_state["last_error"] = str(error)
    if error.code == "selection_conflict":
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


def sidebar():
    with st.sidebar:
        st.caption("ATOMIZED STUDIOS")
        st.button("New Research", on_click=new_research, width="stretch")
        if "recent_runs" not in st.session_state:
            st.session_state["recent_runs"] = client.request("GET", "/search-runs")
        recent = st.session_state["recent_runs"]
        recent = [item for item in recent if item.get("mode") == "live"]
        st.caption("RECENT RESEARCH")
        if recent:
            with st.container(key="recent_research"):
                for item in recent:
                    active = item["id"] == st.session_state.get("run_id")
                    title = item.get("title") or item["summary"][:48]
                    title_column, menu_column = st.columns([5, 1], gap="xxsmall", wrap=False)
                    if title_column.button(
                        title,
                        key=f"research_{item['id']}",
                        width="stretch",
                        type="primary" if active else "tertiary",
                        help=f"{title} · {item['created_at'][:10]}",
                    ):
                        activate_run(item["id"])
                        st.rerun()
                    with menu_column.popover(
                        "...",
                        key=f"manage_{item['id']}",
                        help=f"Manage {title}",
                        type="tertiary",
                        width="stretch",
                    ):
                        st.button(
                            "Rename",
                            key=f"rename_{item['id']}",
                            width="stretch",
                            on_click=open_management,
                            args=("rename", item["id"], title),
                        )
                        st.button(
                            "Delete",
                            key=f"delete_{item['id']}",
                            width="stretch",
                            help="Delete research after confirmation. Running searches must finish first.",
                            on_click=open_management,
                            args=("delete", item["id"], title),
                        )
        else:
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
        submitted = remove.button("Delete Research", type="primary", width="stretch")
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
    if pending.get("strategy") is None:
        with st.spinner("Understanding your idea..."):
            pending["strategy"] = client.request("POST", f"/briefs/{pending['brief']['id']}/strategy")
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
    st.rerun()


def brief_form(health):
    with st.form("brief_form"):
        concept = st.text_area(
            "Describe your creative idea",
            height=180,
            placeholder="A short film that makes a complex idea feel human...",
        )
        with st.expander("Advanced Options"):
            sources = st.multiselect("Sources", SOURCES, default=SOURCES)
            count = st.number_input(
                "Reference Count", 1, health["max_references"], min(10, health["max_references"])
            )
            constraints = st.text_area(
                "Creative Constraints",
                height=90,
                placeholder="Budget, timing, required elements, or anything to avoid (optional)",
            )
        submit = st.form_submit_button("Find References", type="primary")
    if submit:
        if not concept.strip() or not sources:
            st.error("Describe your idea and select at least one source.")
            return
        if not health["live_search_ready"]:
            st.error("The search service is unavailable. Please contact the workspace administrator.")
            return
        brief = client.request(
            "POST",
            "/briefs",
            {
                "concept": concept,
                "constraints": constraints,
                "preferred_sources": sources,
                "requested_count": int(count),
            },
        )
        pending = {"brief": brief, "strategy": None, "submission_key": str(uuid4())}
        st.session_state["pending_research"] = pending
        close_detail()
        submit_pending()


def resume_search():
    st.info("Your idea is saved. Continue your search without submitting it again.")
    if st.button("Continue Search", type="primary"):
        submit_pending()


@st.fragment(run_every="2s")
def poll_run(run_id):
    try:
        run = client.request("GET", f"/search-runs/{run_id}")
        st.session_state["run"] = run
        if run["status"] not in {"queued", "running"}:
            st.rerun()
        stage = run["stage"]
        message = "Finding references for your idea..."
        if stage == "queued":
            message = "Your research is queued..."
        elif stage == "organizing_search_evidence":
            message = "Putting your references together..."
        elif stage == "checking_public_pages":
            message = "Checking source pages..."
        elif stage == "ranking_and_deduplicating":
            message = "Selecting distinct, useful references..."
        st.info(message)
    except APIError as error:
        display_error(error)


def presentation_for(run):
    presentations = st.session_state.setdefault("presentations", {})
    if run["id"] not in presentations:
        try:
            presentations[run["id"]] = client.request("GET", f"/search-runs/{run['id']}/presentation")
        except APIError:
            # Preview enrichment never prevents browsing an existing result or exporting it.
            presentations[run["id"]] = {"previews": {}}
    return presentations[run["id"]]


def reference_card(run, item, draft, previews):
    run_id, item_id = run["id"], item["id"]
    with st.container(border=True, key=f"reference_card_{item_id}"):
        st.html(preview_markup(previews.get(item_id), item["title"]))
        st.markdown(f"#### {item['title']}")
        st.caption(f"{item['platform']} / {item['strand']}")
        annotation = draft["annotations"][item_id]
        st.markdown("**What to Borrow:** " + borrow_summary(annotation["what_to_borrow"]))
        with st.container(key=f"card_actions_{item_id}"):
            details, link, selection = st.columns([6, 3, 1.2], gap="xsmall", wrap=False)
            details.button(
                "Details",
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
            selected = item_id in draft["ordered_ids"]
            with selection.container(key=f"card_select_{item_id}"):
                st.button(
                    "Remove from Pack" if selected else "Add to Pack",
                    icon=":material/check:" if selected else ":material/add:",
                    key=f"pick_action_{run_id}_{item_id}",
                    width=40,
                    type="primary" if selected else "secondary",
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
    st.html(preview_markup(preview, item["title"]))
    original, selection = st.columns(2)
    original.link_button("Open Link", item["source_url"], width="stretch")
    selected = item_id in draft_for(run)["ordered_ids"]
    if selection.button(
        "Remove from Pack" if selected else "Add to Pack",
        key=f"detail_select_{item_id}",
        width="stretch",
        on_click=toggle_reference,
        args=(run_id, item_id),
    ):
        st.rerun()
    for field, label in EDIT_FIELDS.items():
        st.markdown(f"**{label}**")
        st.write(annotation[field])
    with st.expander("Source Evidence, Verification and Rights"):
        st.markdown("**Rights and usage**")
        st.write(annotation["rights_note"])
        if preview and preview.get("image"):
            st.caption(
                preview["basis"] + ". Preview use does not establish video or image reproduction rights."
            )
            st.link_button("Thumbnail Source", preview["source_url"])
        st.caption(
            "Source descriptions report evidence; borrowing ideas, route fit and production notes "
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
    with st.expander("Recommendation details and assumptions"):
        if item.get("ranking"):
            rank = item["ranking"]
            st.caption(f"Recommendation fit: {rank['score']:.0f}/100 / {rank['role'].replace('_', ' ')}")
            for reason in rank["reasons"]:
                st.write(reason)
            st.write(rank["selection_note"])
        for assumption in annotation.get("assumptions", []):
            st.write(assumption)
    done, exclude = st.columns(2)
    if done.button("Done", type="primary", width="stretch"):
        close_detail()
        st.rerun()
    if exclude.button(
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
    record = client.request(
        "POST", f"/search-runs/{run['id']}/exports/docx", {"expected_selection_version": saved["version"]}
    )
    if record["selection_version"] != saved["version"]:
        raise APIError("The saved pack changed before export.", code="selection_conflict", status=409)
    content = client.request("GET", f"/exports/{record['id']}/download", binary=True)
    st.session_state.setdefault("pack_downloads", {})[run["id"]] = {
        "content": content,
        "version": saved["version"],
        "revision": draft["revision"],
    }


def selection_panel(run, draft):
    st.subheader(f"Your Pack ({len(draft['ordered_ids'])})", anchor="your-pack")
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
            "Download Reference Pack (.docx)",
            download["content"],
            type="primary",
            file_name=f"Atomized-Reference-Pack-{run['id'][:8]}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            width="stretch",
        )


def result_panel():
    run = st.session_state["run"]
    if run.get("mode") != "live":
        st.info("This archived demonstration is read-only. Start new research to find current references.")
        return
    if run["status"] in {"queued", "running"}:
        poll_run(run["id"])
        return
    if run["status"] not in {"completed", "partial"}:
        if run.get("error_code") in {"no_eligible_references", "no_search_evidence"}:
            st.error(
                "No usable references remained after source checks. Try a different idea or broader sources."
            )
        else:
            st.error("We could not complete this research. Please try a new search.")
        return
    draft = draft_for(run)
    presentation = presentation_for(run)
    if run["status"] == "partial":
        st.info(f"Found {len(run['results'])} references. Some sources need review or had limited coverage.")
    st.html('<a class="rf-pack-shortcut" href="#your-pack">View Your Pack</a>')
    with st.container(key="research_workspace"):
        results, pack = st.columns([7, 3], gap="medium")
        with results, st.container(gap="xsmall"):
            with st.container(key="results_summary", gap="xxsmall"):
                active_filters = len(st.session_state.get(f"source_filter_{run['id']}", [])) + len(
                    st.session_state.get(f"strand_filter_{run['id']}", [])
                )
                with st.container(
                    key=f"results_heading_{run['id']}",
                    horizontal=True,
                    wrap=False,
                    gap="small",
                    vertical_alignment="center",
                ):
                    st.subheader("Reference Results")
                    with st.popover(
                        str(active_filters) if active_filters else "",
                        icon=":material/tune:",
                        type="tertiary",
                        key=f"result_filters_{run['id']}",
                        help=f"Filter references ({active_filters} selected values)"
                        if active_filters
                        else "Filter references",
                    ):
                        platforms = sorted({item["platform"] for item in run["results"]})
                        strands = sorted({item["strand"] for item in run["results"]})
                        source_filter = st.multiselect("Source", platforms, key=f"source_filter_{run['id']}")
                        strand_filter = st.multiselect(
                            "Creative Category", strands, key=f"strand_filter_{run['id']}"
                        )
                        st.button(
                            "Clear Filters",
                            key=f"clear_filters_{run['id']}",
                            disabled=not active_filters,
                            on_click=clear_filters,
                            args=(run["id"],),
                            width="stretch",
                        )
                        if draft.get("rejected_ids"):
                            st.divider()
                            st.caption(f"{len(draft['rejected_ids'])} references excluded.")
                            st.button(
                                "Restore Excluded References", on_click=restore_excluded, args=(run["id"],)
                            )
                visible = [
                    item
                    for item in run["results"]
                    if item["id"] not in draft.get("rejected_ids", [])
                    and (not source_filter or item["platform"] in source_filter)
                    and (not strand_filter or item["strand"] in strand_filter)
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
            if not visible:
                st.info(
                    "No references match these filters. Adjust the filters or restore excluded references."
                )
            for start in range(0, len(visible), 2):
                with st.container(key=f"reference_grid_{start}"):
                    columns = st.columns(2, gap="small")
                    for column, item in zip(columns, visible[start : start + 2]):
                        with column:
                            reference_card(run, item, draft, presentation["previews"])
        with pack:
            with st.container(key="pack_panel", border=True):
                selection_panel(run, draft)


def main():
    stylesheet = Path(__file__).with_name("workspace.css").read_text(encoding="utf-8")
    st.html(f"<style>{stylesheet}</style>")
    st.title("Reference Finder")
    st.write("Turn your creative idea into a curated reference pack.")
    try:
        if "workspace_health" not in st.session_state:
            st.session_state["workspace_health"] = client.request("GET", "/health")
        health = st.session_state["workspace_health"]
        if "mode" not in health or "max_references" not in health:
            raise APIError("This service is not the Reference Finder backend.")
        if not st.session_state.get("run_id") and st.query_params.get("run"):
            try:
                activate_run(st.query_params["run"])
            except APIError:
                st.query_params.clear()
                st.warning("That saved research is unavailable. Start a new search below.")
        sidebar()
        if st.session_state.get("pending_research"):
            resume_search()
        elif st.session_state.get("run"):
            result_panel()
        else:
            brief_form(health)
        detail = st.session_state.get("active_detail")
        run = st.session_state.get("run")
        if st.session_state.get("research_management"):
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
