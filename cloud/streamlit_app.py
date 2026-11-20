"""Single Streamlit Community Cloud entrypoint for the existing frontend and API."""

import os
import runpy
import sys
from pathlib import Path

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from frontend.access import require_access  # noqa: E402
from frontend.cloud_runtime import CloudStartupError, cloud_settings, ensure_backend  # noqa: E402


def main():
    st.set_page_config(page_title="Reference Finder", page_icon="A", layout="wide")
    require_access()
    try:
        try:
            secrets = st.secrets.to_dict()
        except StreamlitSecretNotFoundError:
            secrets = {}
        backend = ensure_backend(cloud_settings(secrets))
    except CloudStartupError as error:
        st.title("Atomized Reference Finder")
        st.error(str(error))
        st.stop()
    os.environ["BACKEND_URL"] = backend.url
    runpy.run_path(str(ROOT / "frontend" / "app.py"), run_name="__main__")


if __name__ == "__main__":
    main()
