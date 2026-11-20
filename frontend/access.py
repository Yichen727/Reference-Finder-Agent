"""Server-side, session-scoped access gate for the shared workspace."""

import hashlib
import hmac
import os
from time import monotonic

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

GRANT_KEY = "_workspace_access_grant"
INPUT_KEY = "_workspace_access_code"
ATTEMPT_LIMIT = 5
COOLDOWN_SECONDS = 30


def configured_access_code() -> str | None:
    """Keep the code in private configuration, never in the repository."""
    value = os.environ.get("APP_ACCESS_CODE")
    if value is None:
        try:
            value = st.secrets.to_dict().get("APP_ACCESS_CODE")
        except StreamlitSecretNotFoundError:
            return None
    return value if isinstance(value, str) and value.strip() else None


def _signature(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _check_code(code: str) -> None:
    submitted = st.session_state.pop(INPUT_KEY, "")
    if monotonic() < st.session_state.get("_access_retry_at", 0):
        return
    if hmac.compare_digest(submitted.encode("utf-8"), code.encode("utf-8")):
        st.session_state[GRANT_KEY] = _signature(code)
        for key in ("_access_attempts", "_access_retry_at", "_access_error"):
            st.session_state.pop(key, None)
        return
    attempts = st.session_state.get("_access_attempts", 0) + 1
    st.session_state["_access_attempts"] = attempts
    st.session_state["_access_error"] = "Incorrect security code. Please try again."
    if attempts >= ATTEMPT_LIMIT:
        st.session_state["_access_retry_at"] = monotonic() + COOLDOWN_SECONDS
        st.session_state["_access_attempts"] = 0


@st.dialog("Unlock Reference Finder", dismissible=False)
def _unlock_dialog(code: str) -> None:
    # A form callback runs before the dialog fragment is rerendered. Unlock the
    # whole app, including the cloud entry, only after server-side verification.
    if st.session_state.get(GRANT_KEY) == _signature(code):
        st.rerun(scope="app")
    st.write("Enter the security code to access the workspace.")
    waiting = monotonic() < st.session_state.get("_access_retry_at", 0)
    if waiting:
        st.warning("Too many attempts. Wait 30 seconds, then select Try Again.")
    elif st.session_state.get("_access_error"):
        st.error(st.session_state["_access_error"])
    with st.form("workspace_unlock", clear_on_submit=True):
        st.text_input("Security code", type="password", key=INPUT_KEY,
                      max_chars=128, disabled=waiting)
        st.form_submit_button("Unlock", type="primary", width="stretch",
                              disabled=waiting, on_click=_check_code, args=(code,))
    if waiting:
        st.button("Try Again", key="access_retry")


def require_access() -> None:
    """Stop before data loading or API startup until this session is unlocked."""
    code = configured_access_code()
    if code and st.session_state.get(GRANT_KEY) == _signature(code):
        return
    st.session_state.pop(GRANT_KEY, None)
    st.title("Reference Finder")
    st.caption("Turn your creative idea into a curated reference pack.")
    if code is None:
        st.error("Access is not configured. Add APP_ACCESS_CODE as a quoted string "
                 "in Streamlit Secrets, then restart the app.")
    else:
        _unlock_dialog(code)
    st.stop()
