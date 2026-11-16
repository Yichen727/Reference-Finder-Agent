import copy
from uuid import uuid4

import streamlit as st

from frontend.api_client import APIClient, APIError

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
st.set_page_config(page_title="Atomized / Reference Finder", page_icon="A", layout="wide")


def clear_editor():
    for key in list(st.session_state):
        if key.startswith(("pick_", "edit_", "order_", "download_")):
            del st.session_state[key]
    st.session_state.pop("download", None)


def activate_run(run_id: str):
    run = client.request("GET", f"/search-runs/{run_id}")
    brief = client.request("GET", f"/briefs/{run['brief_id']}")
    clear_editor()
    st.session_state.update(run_id=run_id, run=run, brief=brief)
    st.query_params["run"] = run_id


def sidebar(health):
    with st.sidebar:
        st.markdown("### ATOMIZED\nCreative research workspace")
        st.divider()
        st.caption("Workspace status")
        st.write("MOCK / Demonstration" if health["mode"] == "mock" else "LIVE / External research")
        st.caption(f"Up to {health['max_references']} references / Saved locally")
        if health["mode"] == "mock":
            st.info("Explore the workflow with fictional examples. No API key is needed.")
        else:
            st.info(
                "Research across public video platforms, original work pages and official award archives."
            )
            st.caption("Instagram and Pinterest coverage depends on public indexing and access.")
            models = health.get("models", {})
            st.caption(
                f"Planning: {models.get('planning', 'configured')} / "
                f"Search: {models.get('search', 'configured')}"
            )
            st.caption(
                "Web Search verified in this session"
                if health.get("live_search_verified")
                else "Search availability is verified by a successful research run."
            )
        st.caption(
            "OpenAI configuration ready"
            if health["openai_configured"]
            else "OpenAI key or model not configured"
        )
        if st.button("Check OpenAI connection", disabled=not health["openai_configured"], width="stretch"):
            with st.spinner("Checking model access and structured output..."):
                result = client.request("POST", "/providers/openai/check")
            st.success(f"{result['model']} connected; structured output is available.")
        st.caption("This check makes one small model request.")
        st.divider()
        recent = client.request("GET", "/search-runs")
        if recent:
            options = {
                item["id"]: f"{item['created_at'][:16]} / {item['mode']} / {item['summary'][:32]}"
                for item in recent
            }
            chosen = st.selectbox("Saved research", list(options), format_func=options.get)
            if st.button("Open saved run", width="stretch"):
                activate_run(chosen)
                st.rerun()
        if st.button("New research", width="stretch"):
            for key in ["run_id", "run", "brief", "strategy", "strategy_editor", "submission_key"]:
                st.session_state.pop(key, None)
            clear_editor()
            st.query_params.clear()
            st.rerun()


def brief_form(health):
    st.markdown("#### 01 / Define your creative route")
    st.caption("Start with the idea. Add audience, tone and production constraints where useful.")
    with st.form("brief_form"):
        concept = st.text_area(
            "Creative concept *",
            placeholder="A character-led film using gentle humour to explain a complex sustainability issue.",
            height=110,
        )
        left, right = st.columns(2)
        purpose = left.text_input("Research purpose", placeholder="Pitch / treatment / production approach")
        audience = right.text_input("Audience and intended response")
        format_value = left.text_input("Content format", placeholder="Short film / social / animation")
        platform = right.text_input("Distribution platform", placeholder="YouTube / LinkedIn / live event")
        qualities = st.text_input(
            "Tone and creative qualities", placeholder="Warmth, humour, camera, edit, sound..."
        )
        constraints = st.text_area(
            "Production constraints",
            placeholder="Budget range, duration, locations and aspect ratios",
            height=90,
        )
        known = st.text_area("Known references", height=90)
        exclusions = st.text_area("Exclusions", height=90)
        sources = st.multiselect("Preferred sources", SOURCES, default=SOURCES)
        count = st.number_input(
            "Maximum references", 1, health["max_references"], min(12, health["max_references"])
        )
        st.caption("Pack language: English")
        submit = st.form_submit_button("Build research strategy", type="primary")
    if submit:
        if not concept.strip() or not sources:
            st.error("Enter a creative concept and choose at least one source.")
            return
        with st.spinner("Preparing research strands..."):
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
                    "language": "en",
                    "preferred_sources": sources,
                    "requested_count": int(count),
                },
            )
            strategy = client.request("POST", f"/briefs/{brief['id']}/strategy")
        st.session_state.update(brief=brief, strategy=strategy, submission_key=str(uuid4()))
        st.session_state["strategy_editor"] = [
            {"Strand": strand["name"], "Intent": strand["intent"], "Queries": "\n".join(strand["queries"])}
            for strand in strategy["strands"]
        ]
        st.rerun()


def strategy_panel(health):
    strategy = st.session_state["strategy"]
    st.markdown("#### 02 / Review your research strategy")
    st.write(strategy["summary"])
    for assumption in strategy["assumptions"]:
        st.caption(assumption)
    with st.form("strategy_form"):
        st.caption("Edit the strands and queries. Put each query on its own line.")
        rows = st.data_editor(
            st.session_state["strategy_editor"], num_rows="fixed", hide_index=True, width="stretch"
        )
        launch = st.form_submit_button(
            "Generate Mock references" if health["mode"] == "mock" else "Search external references",
            type="primary",
            disabled=health["mode"] == "live" and not health["live_search_ready"],
        )
    if launch:
        edited = copy.deepcopy(strategy)
        edited["strands"] = [
            {
                "name": row["Strand"],
                "intent": row["Intent"],
                "queries": [line.strip() for line in row["Queries"].splitlines() if line.strip()],
            }
            for row in rows
        ]
        run = client.request(
            "POST",
            "/search-runs",
            {
                "brief_id": st.session_state["brief"]["id"],
                "strategy": edited,
                "idempotency_key": st.session_state["submission_key"],
            },
        )
        activate_run(run["id"])
        st.rerun()


@st.fragment(run_every="2s")
def poll_run(run_id):
    try:
        run = client.request("GET", f"/search-runs/{run_id}")
        stage = run["stage"].replace("_", " ")
        st.info(f"Research in progress / {stage}")
        if run.get("mode") == "live":
            st.caption("Searching multiple sources and organising evidence can take a few minutes.")
        if run["status"] not in {"queued", "running"}:
            st.session_state["run"] = run
            st.rerun()
    except APIError as error:
        st.error(str(error))


def result_panel():
    run = st.session_state["run"]
    if run["status"] in {"queued", "running"}:
        poll_run(run["id"])
        return
    st.markdown("#### 03 / Curate your references")
    st.caption(st.session_state["brief"]["concept"])
    for warning in run["warnings"]:
        st.warning(warning)
    if run.get("mode") == "live":
        with st.expander("Source coverage and research usage", expanded=run["status"] == "failed"):
            for item in run.get("coverage", []):
                st.write(
                    f"**{item['category']}** / {item['queries_attempted']} query groups / "
                    f"{item['references_returned']} references"
                )
                st.caption(item["note"])
            usage = run.get("usage", {})
            st.caption(
                f"API requests: {usage.get('requests_attempted', 0)} / "
                f"Web Search calls: {usage.get('web_search_calls', 0)} / "
                f"Tokens: {usage.get('input_tokens', 0)} input + {usage.get('output_tokens', 0)} output / "
                f"Elapsed: {usage.get('elapsed_seconds', 0)}s"
            )
    if run["status"] not in {"completed", "partial"}:
        st.error(f"Run status: {run['status']}. Start a new research run to retry.")
        return
    selection = run.get("selection") or {}
    saved_order = selection.get("ordered_ids", [])
    by_id = {item["id"]: item for item in run["results"]}
    all_ids = saved_order + [item_id for item_id in by_id if item_id not in saved_order]
    c1, c2, c3 = st.columns(3)
    c1.metric("Available references", len(run["results"]))
    c2.metric("Saved selections", len(saved_order))
    c3.metric("Saved version", selection.get("version", 0))
    for item_id in all_ids:
        item = by_id[item_id]
        checkbox_key = f"pick_{run['id']}_{item_id}"
        if checkbox_key not in st.session_state:
            st.session_state[checkbox_key] = item_id in saved_order
        edit_prefix = f"edit_{run['id']}_{item_id}"
        annotation = selection.get("annotation_overrides", {}).get(item_id, item["annotation"])
        for field in ["what_it_is", "what_to_borrow", "why_it_fits", "production_note"]:
            st.session_state.setdefault(f"{edit_prefix}_{field}", annotation[field])
        with st.container(border=True):
            check, info = st.columns([1, 5])
            check.checkbox("Include in pack", key=checkbox_key)
            info.markdown(f"**{item['title']}**")
            info.caption(f"{item['platform']} / {item['strand']} / {' / '.join(item['tags'])}")
            st.write(annotation["what_it_is"])
            st.caption("What to borrow / " + annotation["what_to_borrow"])
            st.link_button(
                "Placeholder link" if item["is_mock"] else "View original source", item["source_url"]
            )
            with st.expander("Review and edit annotations"):
                for field, label in [
                    ("what_it_is", "What it is"),
                    ("what_to_borrow", "What to borrow"),
                    ("why_it_fits", "Why it supports this route"),
                    ("production_note", "Early production indication"),
                ]:
                    st.text_area(label, key=f"{edit_prefix}_{field}")
                st.caption(
                    f"Evidence: {item['verification_status']} / Confidence: {annotation['confidence']}"
                )
                st.caption(annotation["rights_note"])
                if not item["is_mock"]:
                    for evidence in run["evidence"]:
                        if evidence["id"] in item["evidence_ids"]:
                            st.caption(f"{evidence['evidence_type']} / {evidence['retrieved_at']}")
                            st.write(evidence["excerpt"])
                            st.link_button(
                                "Open evidence source",
                                evidence["source_url"],
                                key=f"source_{run['id']}_{item_id}_{evidence['id']}",
                            )
    selected = [item_id for item_id in all_ids if st.session_state.get(f"pick_{run['id']}_{item_id}", False)]
    st.divider()
    st.markdown("#### 04 / Set the order and export")
    if selected:
        st.caption("Edit the order numbers. Ties keep their current order.")
        order_rows = st.data_editor(
            [
                {"Order": index + 1, "Reference": by_id[item_id]["title"], "ID": item_id}
                for index, item_id in enumerate(selected)
            ],
            disabled=["Reference", "ID"],
            hide_index=True,
            width="stretch",
            column_config={
                "ID": None,
                "Order": st.column_config.NumberColumn(min_value=1, max_value=15, step=1, required=True),
            },
            key=f"order_{run['id']}_{'|'.join(selected)}",
        )
        selected = [row["ID"] for row in sorted(order_rows, key=lambda row: row["Order"] or 99)]
    else:
        st.info("Choose at least one reference to export.")
    if st.button("Save selection and annotations", type="primary", key=f"save_{run['id']}"):
        overrides = {}
        for item_id in selected:
            value = copy.deepcopy(
                selection.get("annotation_overrides", {}).get(item_id, by_id[item_id]["annotation"])
            )
            for field in ["what_it_is", "what_to_borrow", "why_it_fits", "production_note"]:
                value[field] = st.session_state[f"edit_{run['id']}_{item_id}_{field}"]
            overrides[item_id] = value
        client.request(
            "PUT",
            f"/search-runs/{run['id']}/selection",
            {
                "ordered_ids": selected,
                "annotation_overrides": overrides,
                "expected_version": selection.get("version", 0),
            },
        )
        activate_run(run["id"])
        st.session_state["saved_notice"] = True
        st.rerun()
    if st.session_state.pop("saved_notice", False):
        st.success("Selection, order and annotations saved.")
    st.caption("DOCX uses your latest saved selection. Save edits before generating a document.")
    if st.button("Generate DOCX from saved selection", disabled=not saved_order):
        record = client.request("POST", f"/search-runs/{run['id']}/exports/docx")
        content = client.request("GET", f"/exports/{record['id']}/download", binary=True)
        st.session_state["download"] = {"content": content, "version": record["selection_version"]}
    if st.session_state.get("download"):
        download = st.session_state["download"]
        st.download_button(
            f"Download Reference Pack / Version {download['version']}",
            download["content"],
            file_name=f"Atomized-Reference-Pack-{run['id'][:8]}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )


def main():
    st.caption("ATOMIZED STUDIOS / CREATIVE RESEARCH")
    st.title("Find the reference. Build the idea.")
    st.write("Turn a creative route into a focused, curated reference pack.")
    st.divider()
    try:
        health = client.request("GET", "/health")
        if "mode" not in health or "max_references" not in health:
            raise APIError("This service is not the Reference Finder backend. Check BACKEND_URL.")
        if not st.session_state.get("run_id") and st.query_params.get("run"):
            try:
                activate_run(st.query_params["run"])
            except APIError as error:
                st.query_params.clear()
                st.warning(f"Could not restore the linked run: {error}")
        sidebar(health)
        if st.session_state.get("run"):
            result_panel()
        elif st.session_state.get("strategy"):
            strategy_panel(health)
        else:
            brief_form(health)
    except APIError as error:
        st.error(str(error))
        if st.button("Retry connection / Reload"):
            st.rerun()


main()
