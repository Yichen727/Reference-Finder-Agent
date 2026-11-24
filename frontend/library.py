"""Global library UI state; all favorites and pack edits are persisted by FastAPI."""

import copy
from uuid import uuid4

import streamlit as st

from frontend.api_client import APIClient, APIError
from frontend.curation import apply_pack_action
from frontend.pack_component import pack_list

client = APIClient()


def enabled():
    return bool(st.session_state.get("workspace_health", {}).get("capabilities", {}).get("global_library"))


def context_id():
    return None if st.session_state.get("view") == "favorites" else st.session_state.get("run_id")


def refresh(run_id=None):
    path = "/library" + (f"?run_id={run_id}" if run_id else "")
    value = client.request("GET", path)
    st.session_state["library_state"] = value
    st.session_state["library_context"] = run_id
    st.session_state.pop("favorites_presentation", None)
    st.session_state.setdefault("pack_drafts", {}).pop("favorites", None)
    return value


def state():
    return st.session_state.get("library_state") or refresh(context_id())


def ensure(run):
    ids = [item["id"] for item in run["results"]]
    if (
        st.session_state.get("library_context") != run["id"]
        or st.session_state.get("library_result_ids") != ids
    ):
        refresh(run["id"])
        st.session_state["library_result_ids"] = ids


def saved_id(run_id, item_id):
    if run_id == "favorites":
        return item_id
    return state()["reference_index"].get(f"{run_id}:{item_id}")


def is_favorite(run_id, item_id):
    return saved_id(run_id, item_id) in {item["id"] for item in state()["favorites"]}


def is_selected(run_id, item_id):
    return saved_id(run_id, item_id) in state()["current_pack"]["ordered_ids"]


def pack_full():
    return (
        len(state()["current_pack"]["ordered_ids"]) >= st.session_state["workspace_health"]["max_references"]
    )


def blocked():
    return bool(st.session_state.get("library_pending"))


def locator(run_id, item_id):
    if run_id == "favorites":
        return {"saved_reference_id": item_id}
    value = {"run_id": run_id, "reference_id": item_id}
    annotation = st.session_state.get("pack_drafts", {}).get(run_id, {}).get("annotations", {}).get(item_id)
    if annotation:
        value["annotation"] = copy.deepcopy(annotation)
    return value


def perform(method, path, payload=None):
    try:
        client.request(method, path, payload)
        refresh(context_id())
    except APIError as error:
        st.session_state["library_pending"] = {"method": method, "path": path, "payload": payload}
        st.session_state["library_error"] = str(error)
        st.session_state["library_conflict"] = error.code in {"pack_conflict", "pack_not_found"}
        return False
    st.session_state.pop("library_pending", None)
    st.session_state.pop("library_error", None)
    st.session_state.pop("library_conflict", None)
    return True


def retry_change():
    value = st.session_state["library_pending"]
    perform(value["method"], value["path"], value["payload"])


def reload_pack():
    refresh(context_id())
    for key in ("library_pending", "library_error", "library_conflict", "library_export_pending"):
        st.session_state.pop(key, None)


def toggle_favorite(run_id, item_id):
    if blocked():
        return
    if is_favorite(run_id, item_id):
        perform("DELETE", f"/favorites/{saved_id(run_id, item_id)}")
    else:
        perform("PUT", "/favorites", locator(run_id, item_id))


def toggle_pack(run_id, item_id):
    if blocked():
        return
    pack = state()["current_pack"]
    if is_selected(run_id, item_id):
        perform(
            "DELETE",
            f"/packs/{pack['id']}/references/{saved_id(run_id, item_id)}",
            {"expected_version": pack["version"]},
        )
    else:
        perform(
            "POST",
            f"/packs/{pack['id']}/references",
            {
                **locator(run_id, item_id),
                "expected_version": pack["version"],
            },
        )


def switch_pack(key):
    if blocked():
        return
    pack_id = st.session_state[key]
    if pack_id != state()["current_pack"]["id"]:
        perform("PUT", f"/packs/{pack_id}/activate")


def reorder(key):
    pack = state()["current_pack"]
    draft = {"ordered_ids": list(pack["ordered_ids"]), "revision": pack["version"]}
    if not blocked() and apply_pack_action(draft, st.session_state.get(key, {}).get("action")):
        perform(
            "PUT",
            f"/packs/{pack['id']}/order",
            {
                "expected_version": pack["version"],
                "ordered_ids": draft["ordered_ids"],
            },
        )


def close_management():
    st.session_state.pop("pack_management", None)


def open_management(action):
    if blocked():
        return
    st.session_state.pop("active_detail", None)
    st.session_state.pop("research_management", None)
    pack = state()["current_pack"]
    st.session_state["pack_management"] = {
        "action": action,
        "pack": copy.deepcopy(pack),
        "key": str(uuid4()),
    }
    st.session_state["pack_name_input"] = "" if action == "create" else pack["name"]


@st.dialog("Manage Pack", width="small", on_dismiss=close_management)
def management_dialog():
    target = st.session_state["pack_management"]
    action, pack = target["action"], target["pack"]
    if action == "delete":
        st.subheader("Delete Pack?")
        st.write(pack["name"])
        st.caption("Favorites, research and existing exports will be kept.")
        confirm, cancel = st.columns(2, gap="small", wrap=False, vertical_alignment="center")
        submitted = confirm.button(
            "Delete Pack", type="primary", key="confirm_pack_delete", width="stretch",
        )
        cancelled = cancel.button("Cancel", key="cancel_pack_management", width="stretch")
    else:
        st.subheader("Create Pack" if action == "create" else "Rename Pack")
        name = st.text_input("Pack name", key="pack_name_input", max_chars=100)
        confirm, cancel = st.columns(2, gap="small", wrap=False, vertical_alignment="center")
        submitted = confirm.button(
            "Create Pack" if action == "create" else "Save Name",
            type="primary", key="submit_pack_management", width="stretch",
        )
        cancelled = cancel.button("Cancel", key="cancel_pack_management", width="stretch")
    if cancelled:
        close_management()
        st.rerun()
    if submitted:
        if action != "delete" and not name.strip():
            st.error("Enter a pack name.")
            return
        if action == "create":
            perform("POST", "/packs", {"name": name.strip(), "idempotency_key": target["key"]})
        elif action == "rename":
            perform(
                "PATCH", f"/packs/{pack['id']}", {"name": name.strip(), "expected_version": pack["version"]}
            )
        else:
            perform("DELETE", f"/packs/{pack['id']}", {"expected_version": pack["version"]})
        close_management()
        st.rerun()


def export_pack():
    pack = state()["current_pack"]
    pending = st.session_state.get("library_export_pending")
    if not pending or (pending["pack_id"], pending["version"]) != (pack["id"], pack["version"]):
        pending = {"pack_id": pack["id"], "version": pack["version"], "record": None}
        st.session_state["library_export_pending"] = pending
    try:
        if pending["record"] is None:
            pending["record"] = client.request(
                "POST",
                f"/packs/{pack['id']}/exports/pptx",
                {
                    "expected_version": pack["version"],
                },
            )
        record = pending["record"]
        if record.get("pack_id") != pack["id"] or record["selection_version"] != pack["version"]:
            raise APIError("The export does not match the displayed pack.", code="pack_conflict", status=409)
        content = client.request("GET", f"/exports/{record['id']}/download", binary=True)
        st.session_state.setdefault("library_downloads", {})[pack["id"]] = {
            "content": content,
            "version": pack["version"],
        }
    except APIError as error:
        st.session_state["library_error"] = str(error)
        st.session_state["library_conflict"] = error.code in {"pack_conflict", "pack_not_found"}
        return
    st.session_state.pop("library_export_pending", None)
    st.session_state.pop("library_error", None)


def render_current_pack():
    value = state()
    pack = value["current_pack"]
    st.subheader(f"Current Pack ({len(pack['ordered_ids'])})", anchor="your-pack")
    names = {item["id"]: item["name"] for item in value["packs"]}
    labels = {
        key: name if list(names.values()).count(name) == 1 else f"{name} · {key[:4]}"
        for key, name in names.items()
    }
    choice_key = f"current_pack_choice_{pack['id']}"
    with st.container(horizontal=True, wrap=False, gap="xsmall", vertical_alignment="center"):
        st.selectbox(
            "Current Pack",
            list(names),
            index=list(names).index(pack["id"]),
            format_func=labels.get,
            key=choice_key,
            on_change=switch_pack,
            args=(choice_key,),
            label_visibility="collapsed",
            disabled=blocked(),
        )
        with st.popover(
            "⋯",
            width=32,
            disabled=blocked(),
            type="tertiary",
            key=f"pack_options_{pack['id']}",
            help="Manage Pack",
        ):
            with st.container(key=f"management_menu_pack_{pack['id']}", gap="xxsmall"):
                for action, label, icon in (
                    ("create", "Create Pack", ":material/add:"),
                    ("rename", "Rename Pack", ":material/edit:"),
                    ("delete", "Delete Pack", ":material/delete:"),
                ):
                    if action == "delete":
                        st.divider()
                    st.button(
                        label,
                        key=f"pack_manage_{action}",
                        on_click=open_management,
                        args=(action,),
                        icon=icon, icon_position="left", type="tertiary",
                        width="stretch",
                    )
    st.caption("Drag a row to reorder")
    key = f"global_pack_sort_{pack['id']}"
    pack_list(
        data={
            "items": [
                {"id": item_id, "title": pack["references"][item_id]["reference"]["title"]}
                for item_id in pack["ordered_ids"]
            ],
            "revision": pack["version"],
        },
        key=key,
        on_action_change=lambda: reorder(key),
    )
    if not pack["ordered_ids"]:
        st.caption("Add references from any research or Favorites.")
    if st.session_state.get("library_error"):
        st.error(
            "The request could not be completed. Your displayed pack is kept. "
            + st.session_state["library_error"]
        )
        if st.session_state.get("library_conflict"):
            st.caption("Loading the latest pack discards the pending change.")
            st.button("Load Latest Pack", on_click=reload_pack)
        elif blocked():
            st.button("Retry Change", on_click=retry_change)
            st.button(
                "Reload Saved State",
                on_click=reload_pack,
                help="Discard the pending change and load saved Favorites and Packs.",
            )
    download = st.session_state.get("library_downloads", {}).get(pack["id"])
    if st.button(
        "Export Reference Pack",
        type="primary",
        width="stretch",
        disabled=not pack["ordered_ids"] or blocked(),
    ):
        with st.spinner("Preparing your document..."):
            export_pack()
        st.rerun()
    if download and download["version"] == pack["version"]:
        st.download_button(
            "Download Reference Pack (.pptx)",
            download["content"],
            width="stretch",
            file_name=f"Atomized-{pack['name']}.pptx",
            mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )


def favorites_context():
    value = state()
    results, evidence = [], {}
    for saved in value["favorites"]:
        snapshot = saved["snapshots"][saved["favorite_snapshot"]]
        results.append({**copy.deepcopy(snapshot["reference"]), "id": saved["id"]})
        evidence.update({item["id"]: item for item in snapshot["evidence"]})
    return {
        "id": "favorites",
        "mode": "live",
        "status": "completed",
        "results": results,
        "evidence": list(evidence.values()),
        "selection": None,
    }


def favorites_previews():
    if ("favorites_presentation" not in st.session_state
            or st.session_state.get("favorites_presentation_version") != 7):
        st.session_state["favorites_presentation"] = client.request("GET", "/library/presentation")
        st.session_state["favorites_presentation_version"] = 7
    return st.session_state["favorites_presentation"].get("previews", {})
