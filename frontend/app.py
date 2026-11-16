from uuid import uuid4

import streamlit as st

from frontend.api_client import APIClient, APIError
from frontend.curation import EDIT_FIELDS, draft_from_run, selection_payload
from frontend.layout import WORKSPACE_STYLE, borrow_summary

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


def edit_annotation(run_id: str, item_id: str, field: str, key: str):
    st.session_state["pack_drafts"][run_id]["annotations"][item_id][field] = st.session_state[key]
    changed(run_id)


def move_reference(run_id: str, item_id: str, offset: int):
    order = st.session_state["pack_drafts"][run_id]["ordered_ids"]
    index = order.index(item_id)
    other = index + offset
    if 0 <= other < len(order):
        order[index], order[other] = order[other], order[index]
        changed(run_id)


def remove_reference(run_id: str, item_id: str):
    st.session_state["pack_drafts"][run_id]["ordered_ids"].remove(item_id)
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


def close_detail():
    st.session_state.pop("active_detail", None)


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
    close_detail()
    st.session_state.pop("pending_research", None)
    st.query_params["run"] = run_id


def new_research():
    close_detail()
    for key in ["run_id", "run", "brief", "pending_research", "last_error"]:
        st.session_state.pop(key, None)
    st.query_params.clear()


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
    elif error.status == 409:
        st.error(str(error).split(" (")[0])
    else:
        st.error("We could not complete that request. Please try again. Your current edits are kept.")


def sidebar():
    with st.sidebar:
        st.caption("ATOMIZED STUDIOS")
        st.button("New Research", on_click=new_research, width="stretch")
        recent = client.request("GET", "/search-runs")
        recent = [item for item in recent if item.get("mode") == "live"]
        if recent:
            options = {item["id"]: f"{item['summary'][:48]} / {item['created_at'][:10]}" for item in recent}
            selected = st.selectbox("Recent Research", list(options), format_func=options.get)
            if st.button("Open Research", width="stretch"):
                activate_run(selected)
                st.rerun()
        else:
            st.caption("No saved research yet.")


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
                "Reference Count", 1, health["max_references"], min(12, health["max_references"])
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


def reference_card(run, item, draft):
    run_id, item_id = run["id"], item["id"]
    with st.container(border=True):
        thumbnail = item.get("thumbnail")
        if thumbnail and thumbnail.get("usage_status") == "approved" and thumbnail.get("available") is True:
            st.image(thumbnail["url"], width="stretch")
        st.markdown(f"#### {item['title']}")
        st.caption(f"{item['platform']} / {item['strand']}")
        annotation = draft["annotations"][item_id]
        st.markdown("**What to Borrow**")
        st.write(borrow_summary(annotation["what_to_borrow"]))
        link, select, details = st.columns(3)
        link.link_button("View Original", item["source_url"], width="stretch")
        key = f"pick_{run_id}_{item_id}"
        st.session_state.setdefault(key, item_id in draft["ordered_ids"])
        select.checkbox("Add to Pack", key=key, on_change=select_reference, args=(run_id, item_id, key))
        details.button(
            "View Details",
            key=f"details_{item_id}",
            width="stretch",
            on_click=open_detail,
            args=(run_id, item_id),
        )


@st.dialog("Reference Details", width="large", on_dismiss=close_detail)
def reference_details(run, item):
    run_id, item_id = run["id"], item["id"]
    annotation = draft_for(run)["annotations"][item_id]
    st.subheader(item["title"])
    st.caption(f"{item['platform']} / {item['strand']}")
    if item.get("creator"):
        st.caption(f"Creator: {item['creator']}")
    if item["tags"]:
        st.caption(" / ".join(item["tags"]))
    st.link_button("View Original", item["source_url"])
    st.caption("Edits are kept in this browser session. Export your pack to save them.")
    for field, label in EDIT_FIELDS.items():
        edit_key = f"edit_{run_id}_{item_id}_{field}"
        st.session_state.setdefault(edit_key, annotation[field])
        st.text_area(label, key=edit_key, on_change=edit_annotation, args=(run_id, item_id, field, edit_key))
    st.markdown("**Rights and usage**")
    st.write(annotation["rights_note"])
    thumbnail = item.get("thumbnail")
    if thumbnail:
        st.caption(f"Thumbnail source: {thumbnail['source_url']} / Usage: {thumbnail['usage_status']}")
    with st.expander("Source verification and evidence"):
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
    for index, item_id in enumerate(draft["ordered_ids"]):
        st.write(by_id[item_id]["title"])
        with st.container(horizontal=True, gap="xxsmall"):
            st.button(
                "↑",
                help="Move up",
                key=f"up_{item_id}",
                disabled=index == 0,
                on_click=move_reference,
                args=(run["id"], item_id, -1),
            )
            st.button(
                "↓",
                help="Move down",
                key=f"down_{item_id}",
                disabled=index == len(draft["ordered_ids"]) - 1,
                on_click=move_reference,
                args=(run["id"], item_id, 1),
            )
            st.button(
                "Remove",
                key=f"remove_{item_id}",
                on_click=remove_reference,
                args=(run["id"], item_id),
            )
    if not draft["ordered_ids"]:
        st.caption("Add references to build your pack.")
    if st.session_state.get("conflict_run") == run["id"]:
        st.info("Load the saved pack to resolve the version conflict. This replaces your local edits.")
        if st.button("Load Saved Pack"):
            activate_run(run["id"], reload_saved=True)
            st.rerun()
    download = st.session_state.setdefault("pack_downloads", {}).get(run["id"])
    st.caption("Export as an editable DOCX document.")
    if st.button(
        "Export Reference Pack",
        type="secondary" if download else "primary",
        disabled=not draft["ordered_ids"],
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
        )


def result_panel(health):
    run = st.session_state["run"]
    with st.expander("New Search"):
        brief_form(health)
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
    st.subheader("Reference Results")
    st.caption(st.session_state["brief"]["concept"])
    if run["status"] == "partial":
        st.info(f"Found {len(run['results'])} references. Some sources need review or had limited coverage.")
    with st.expander("Filter Results"):
        platforms = sorted({item["platform"] for item in run["results"]})
        strands = sorted({item["strand"] for item in run["results"]})
        source_filter = st.multiselect("Source", platforms, key=f"source_filter_{run['id']}")
        strand_filter = st.multiselect("Creative Category", strands, key=f"strand_filter_{run['id']}")
        if draft.get("rejected_ids"):
            st.caption(f"{len(draft['rejected_ids'])} references excluded.")
            st.button("Restore Excluded References", on_click=restore_excluded, args=(run["id"],))
    visible = [
        item
        for item in run["results"]
        if item["id"] not in draft.get("rejected_ids", [])
        and (not source_filter or item["platform"] in source_filter)
        and (not strand_filter or item["strand"] in strand_filter)
    ]
    if not visible:
        st.info("No references match these filters. Adjust the filters or restore excluded references.")
    st.html('<a class="rf-pack-shortcut" href="#your-pack">View Your Pack</a>')
    with st.container(key="research_workspace"):
        results, pack = st.columns([3, 1], gap="medium")
        with results:
            for item in visible:
                reference_card(run, item, draft)
        with pack:
            with st.container(key="pack_panel", border=True):
                selection_panel(run, draft)


def main():
    st.html(WORKSPACE_STYLE)
    st.title("Reference Finder")
    st.write("Turn your creative idea into a curated reference pack.")
    try:
        health = client.request("GET", "/health")
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
            result_panel(health)
        else:
            brief_form(health)
        detail = st.session_state.get("active_detail")
        run = st.session_state.get("run")
        if detail and run and detail[0] == run["id"]:
            item = next((item for item in run["results"] if item["id"] == detail[1]), None)
            if item:
                reference_details(run, item)
    except APIError as error:
        display_error(error)
        if st.button("Retry"):
            st.rerun()


main()
