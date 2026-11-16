from uuid import uuid4

import streamlit as st

from frontend.api_client import APIClient, APIError
from frontend.curation import EDIT_FIELDS, draft_from_run, selection_payload

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
    st.session_state.pop("pending_research", None)
    st.query_params["run"] = run_id


def new_research():
    for key in ["run_id", "run", "brief", "pending_research", "last_error"]:
        st.session_state.pop(key, None)
    st.query_params.clear()


def display_error(error: APIError):
    st.session_state["last_error"] = str(error)
    if error.code == "selection_conflict":
        st.session_state["conflict_run"] = st.session_state.get("run_id")
        st.error("This saved pack changed in another session. Load the saved version before exporting.")
    elif error.code in {"openai_not_configured", "openai_authentication", "openai_permission"}:
        st.error("Search is unavailable. Check the connection in Developer Settings.")
    elif error.code in {"openai_rate_limit", "openai_quota"}:
        st.error("The search service has reached its usage limit. Please try again later.")
    elif error.code == "validation_error":
        st.error("Please check your entries and try again.")
    elif error.status == 409:
        st.error(str(error).split(" (")[0])
    else:
        st.error("We could not complete that request. Please try again. Your current edits are kept.")


def sidebar(health):
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
        st.caption("Live search")
        with st.expander("Developer Settings"):
            st.json(
                {
                    "models": health.get("models", {}),
                    "configured": health["openai_configured"],
                    "search_verified": health.get("live_search_verified", False),
                }
            )
            if st.button("Check Connection", disabled=not health["openai_configured"]):
                with st.spinner("Checking connection..."):
                    client.request("POST", "/providers/openai/check")
                st.success("OpenAI is connected.")
            st.caption("This check makes one small model request.")
            run = st.session_state.get("run")
            if run:
                st.json(
                    {
                        "run_id": run["id"],
                        "status": run["status"],
                        "stage": run["stage"],
                        "usage": run.get("usage", {}),
                        "coverage": run.get("coverage", []),
                        "page_checks": run.get("page_checks", []),
                    }
                )
                for warning in run.get("warnings", []):
                    st.caption(warning)
                st.caption(
                    "Pack edits are kept in this browser session. Export saves them to the research record."
                )
            if st.session_state.get("last_error"):
                st.code(st.session_state["last_error"], language=None)


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
            left, right = st.columns(2)
            purpose = left.text_input("Purpose")
            audience = right.text_input("Audience")
            format_value = left.text_input("Format")
            platform = right.text_input("Platform")
            qualities = st.text_input("Tone and creative qualities")
            constraints = st.text_area("Production constraints", height=90)
            known = st.text_area("Known references", height=90)
            exclusions = st.text_area("Exclusions", height=90)
            sources = st.multiselect("Sources", SOURCES, default=SOURCES)
            count = st.number_input(
                "Maximum references", 1, health["max_references"], min(12, health["max_references"])
            )
            manual = st.checkbox("Edit Search Strategy")
        submit = st.form_submit_button("Find References", type="primary")
    if submit:
        if not concept.strip() or not sources:
            st.error("Describe your idea and select at least one source.")
            return
        if not health["live_search_ready"]:
            st.error("Search is unavailable. Check the connection in Developer Settings.")
            return
        brief = client.request(
            "POST",
            "/briefs",
            {
                "concept": concept,
                "purpose": purpose,
                "audience": audience,
                "format": format_value,
                "platform": platform,
                "qualities": qualities,
                "constraints": constraints,
                "known_references": known,
                "exclusions": exclusions,
                "preferred_sources": sources,
                "requested_count": int(count),
            },
        )
        pending = {"brief": brief, "strategy": None, "submission_key": str(uuid4()), "manual": manual}
        st.session_state["pending_research"] = pending
        if manual:
            with st.spinner("Understanding your idea..."):
                pending["strategy"] = client.request("POST", f"/briefs/{brief['id']}/strategy")
            st.rerun()
        submit_pending()


def strategy_panel():
    pending = st.session_state["pending_research"]
    strategy = pending.get("strategy")
    if strategy is None:
        st.info("Your idea is saved. Continue to prepare the search.")
        if st.button("Continue Search", type="primary"):
            if pending["manual"]:
                pending["strategy"] = client.request("POST", f"/briefs/{pending['brief']['id']}/strategy")
                st.rerun()
            submit_pending()
        return
    if not pending["manual"]:
        st.info("Your search is ready to resume.")
        if st.button("Continue Search", type="primary"):
            submit_pending()
        return
    st.subheader("Edit Search Strategy")
    with st.form("strategy_form"):
        summary = st.text_area("Research direction", value=strategy["summary"])
        strands = []
        for index, strand in enumerate(strategy["strands"]):
            with st.expander(strand["name"], expanded=index == 0):
                name = st.text_input("Strand", value=strand["name"], key=f"strategy_name_{index}")
                intent = st.text_input("Intent", value=strand["intent"], key=f"strategy_intent_{index}")
                queries = st.text_area(
                    "Queries (one per line)",
                    value="\n".join(strand["queries"]),
                    key=f"strategy_queries_{index}",
                )
                strands.append(
                    {
                        "name": name,
                        "intent": intent,
                        "queries": [line.strip() for line in queries.splitlines() if line.strip()],
                    }
                )
        submitted = st.form_submit_button("Find References", type="primary")
    if submitted:
        pending["strategy"] = {**strategy, "summary": summary, "strands": strands}
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
        borrow = annotation["what_to_borrow"]
        st.markdown("**What to Borrow**")
        st.write(borrow if len(borrow) <= 240 else borrow[:237].rstrip() + "...")
        link, select = st.columns(2)
        link.link_button("View Original", item["source_url"], width="stretch")
        key = f"pick_{run_id}_{item_id}"
        st.session_state.setdefault(key, item_id in draft["ordered_ids"])
        select.checkbox("Add to Pack", key=key, on_change=select_reference, args=(run_id, item_id, key))
        with st.expander("Details / Edit"):
            check = item.get("page_check")
            if check:
                st.caption(f"Source page: {check['status']} / {check['reason']}")
                if check.get("final_url") and check["final_url"] != item["source_url"]:
                    st.link_button("Checked Destination", check["final_url"], key=f"checked_{item_id}")
            if item.get("creator"):
                st.caption(f"Creator: {item['creator']}")
            if item["tags"]:
                st.caption(" / ".join(item["tags"]))
            st.caption(
                "Source descriptions report evidence; borrowing ideas, route fit and production notes "
                "are creative interpretations."
            )
            for field, label in EDIT_FIELDS.items():
                edit_key = f"edit_{run_id}_{item_id}_{field}"
                st.session_state.setdefault(edit_key, annotation[field])
                st.text_area(
                    label, key=edit_key, on_change=edit_annotation, args=(run_id, item_id, field, edit_key)
                )
            st.caption(f"Evidence: {item['verification_status']} / Confidence: {annotation['confidence']}")
            st.caption(annotation["rights_note"])
            for assumption in annotation.get("assumptions", []):
                st.caption(assumption)
            if thumbnail:
                st.caption(f"Thumbnail source: {thumbnail['source_url']}")
            for evidence in run["evidence"]:
                if evidence["id"] in item["evidence_ids"]:
                    st.caption(f"{evidence['evidence_type']} / {evidence['retrieved_at']}")
                    st.text(evidence["excerpt"])
                    st.link_button(
                        "Evidence Source",
                        evidence["source_url"],
                        key=f"source_{run_id}_{item_id}_{evidence['id']}",
                    )
            if item.get("ranking"):
                rank = item["ranking"]
                st.caption(f"Recommendation fit: {rank['score']:.0f}/100 / {rank['role'].replace('_', ' ')}")
                for reason in rank["reasons"]:
                    st.caption(reason)
                st.caption(rank["selection_note"])
            for index, url in enumerate(item.get("alternate_urls", [])):
                st.link_button("Alternate Source", url, key=f"alternate_{item_id}_{index}")
            st.button(
                "Exclude Reference",
                key=f"exclude_{item_id}",
                on_click=exclude_reference,
                args=(run_id, item_id),
            )


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
    st.subheader(f"Your Pack ({len(draft['ordered_ids'])})")
    by_id = {item["id"]: item for item in run["results"]}
    for index, item_id in enumerate(draft["ordered_ids"]):
        title, up, down, remove = st.columns([6, 1, 1, 1.5])
        title.write(by_id[item_id]["title"])
        up.button(
            "Up",
            key=f"up_{item_id}",
            disabled=index == 0,
            on_click=move_reference,
            args=(run["id"], item_id, -1),
        )
        down.button(
            "Down",
            key=f"down_{item_id}",
            disabled=index == len(draft["ordered_ids"]) - 1,
            on_click=move_reference,
            args=(run["id"], item_id, 1),
        )
        remove.button("Remove", key=f"remove_{item_id}", on_click=remove_reference, args=(run["id"], item_id))
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
            st.error("We could not complete this research. Start new research or check Developer Settings.")
        return
    draft = draft_for(run)
    st.subheader("References")
    st.caption(st.session_state["brief"]["concept"])
    if run["status"] == "partial":
        st.info(f"Found {len(run['results'])} references. Some sources need review or had limited coverage.")
    with st.expander("Filter Results"):
        platforms = sorted({item["platform"] for item in run["results"]})
        strands = sorted({item["strand"] for item in run["results"]})
        source_filter = st.multiselect("Source", platforms, key=f"source_filter_{run['id']}")
        strand_filter = st.multiselect("Creative Strand", strands, key=f"strand_filter_{run['id']}")
        evidence_filter = st.selectbox(
            "Evidence", ["All", "Page checked", "Needs review"], key=f"evidence_filter_{run['id']}"
        )
        if draft.get("rejected_ids"):
            st.caption(f"{len(draft['rejected_ids'])} references excluded.")
            st.button("Restore Excluded References", on_click=restore_excluded, args=(run["id"],))
    visible = [
        item
        for item in run["results"]
        if item["id"] not in draft.get("rejected_ids", [])
        and (not source_filter or item["platform"] in source_filter)
        and (not strand_filter or item["strand"] in strand_filter)
        and (
            evidence_filter == "All"
            or (item["verification_status"] == "supported") == (evidence_filter == "Page checked")
        )
    ]
    if not visible:
        st.info("No references match these filters. Adjust the filters or restore excluded references.")
    columns = st.columns(2)
    for index, item in enumerate(visible):
        with columns[index % 2]:
            reference_card(run, item, draft)
    st.divider()
    selection_panel(run, draft)


def main():
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
        sidebar(health)
        if st.session_state.get("run"):
            result_panel()
        elif st.session_state.get("pending_research"):
            strategy_panel()
        else:
            brief_form(health)
    except APIError as error:
        display_error(error)
        if st.button("Retry"):
            st.rerun()


main()
