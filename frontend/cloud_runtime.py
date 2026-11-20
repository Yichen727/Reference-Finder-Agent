"""Own one private API worker across Streamlit sessions and reruns."""

import atexit
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import get_origin
from uuid import uuid4

import httpx
import streamlit as st
from pydantic import ValidationError

from backend.core.config import ROOT, Settings


class CloudStartupError(RuntimeError):
    pass


def cloud_settings(secrets):
    overrides = {key.lower(): value for key, value in secrets.items() if key.lower() in Settings.model_fields}
    try:
        for name, value in list(overrides.items()):
            if isinstance(value, str) and get_origin(Settings.model_fields[name].annotation) is list:
                overrides[name] = json.loads(value)
        settings = Settings(_env_file=None, **overrides)
    except (ValueError, TypeError, ValidationError):
        raise CloudStartupError("Check the values in your Streamlit Secrets, then reboot the app.") from None
    if "data_dir" not in settings.model_fields_set:
        settings = settings.model_copy(update={"data_dir": ROOT / "data" / "cloud"})
    if not settings.openai_configured:
        raise CloudStartupError(
            "Add OPENAI_API_KEY to Advanced settings > Secrets in Streamlit Community Cloud, then reboot."
        )
    return settings


def settings_payload(settings):
    value = settings.model_dump(mode="json")
    value["openai_api_key"] = settings.openai_api_key.get_secret_value()
    return value


def settings_fingerprint(settings):
    return hashlib.sha256(json.dumps(settings_payload(settings), sort_keys=True).encode()).hexdigest()


def backend_revision():
    paths = [Path(__file__), ROOT / "pyproject.toml"]
    paths += list((ROOT / "backend").rglob("*.py"))
    paths += list((ROOT / "config").glob("*.py")) + list((ROOT / "config").glob("*.json"))
    paths.append(ROOT / "backend" / "templates" / "manifest.json")
    revision = [(str(path.relative_to(ROOT)), path.stat().st_mtime_ns, path.stat().st_size)
                for path in sorted(paths)]
    return hashlib.sha256(json.dumps(revision).encode()).hexdigest()


class CloudBackend:
    def __init__(self, settings, *, startup_timeout=30):
        self.fingerprint = settings_fingerprint(settings)
        self.revision = backend_revision()
        self.instance = str(uuid4())
        self.process = None
        self.url = ""
        self._close_lock = threading.Lock()
        announced = queue.Queue(maxsize=1)
        deadline = time.monotonic() + startup_timeout
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "backend.cloud_server"], cwd=ROOT,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8",
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

            def read_announcement():
                announced.put(self.process.stdout.readline())
                # Drain any subsequent output so a full pipe cannot block the API.
                for _ in self.process.stdout:
                    pass

            self._reader = threading.Thread(target=read_announcement, name="cloud-api-output", daemon=True)
            self._reader.start()
            self.process.stdin.write(json.dumps({
                "instance": self.instance, "settings": settings_payload(settings),
            }))
            self.process.stdin.write("\n")
            self.process.stdin.flush()
            message = json.loads(announced.get(timeout=max(0.01, deadline - time.monotonic())))
            port = message["port"]
            if type(port) is not int or not 1 <= port <= 65535 or message["instance"] != self.instance:
                raise ValueError("Invalid API announcement")
            self.url = f"http://127.0.0.1:{port}"
            with httpx.Client(base_url=self.url, timeout=0.5, trust_env=False) as client:
                while time.monotonic() < deadline:
                    if not self.running():
                        raise RuntimeError("API exited before becoming ready")
                    try:
                        response = client.get("/_cloud/ready")
                        if response.status_code == 200 and response.json().get("instance") == self.instance:
                            break
                    except (httpx.HTTPError, ValueError):
                        pass
                    time.sleep(0.1)
                else:
                    raise TimeoutError("API startup timed out")
        except Exception:
            self.close()
            raise CloudStartupError(
                "The internal API could not start. Check the Streamlit app logs and reboot the app."
            ) from None
        atexit.register(self.close)

    def running(self):
        return self.process is not None and self.process.poll() is None

    def close(self):
        with self._close_lock:
            if self.process is None:
                return
            try:
                if self.process.stdin and not self.process.stdin.closed:
                    self.process.stdin.close()
            except OSError:
                pass
            try:
                self.process.wait(timeout=7)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            if hasattr(self, "_reader"):
                self._reader.join(timeout=1)
            if self.process.stdout:
                self.process.stdout.close()
            atexit.unregister(self.close)


@st.cache_resource(
    show_spinner="Starting Reference Finder...",
    validate=lambda backend: backend.running(), on_release=lambda backend: backend.close(),
)
def _cached_backend(_settings):
    # Exclude settings from Streamlit's cache key; never cache or log credentials as arguments.
    return CloudBackend(_settings)


def ensure_backend(settings):
    backend = _cached_backend(settings)
    if backend.fingerprint != settings_fingerprint(settings) or backend.revision != backend_revision():
        raise CloudStartupError(
            "App settings or backend files changed. Reboot the app from Streamlit settings to apply them."
        )
    return backend
