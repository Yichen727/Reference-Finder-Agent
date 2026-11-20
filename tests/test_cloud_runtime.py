"""Single-app cloud startup with real loopback HTTP and no OpenAI requests."""

import os
import socket
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from backend.core.config import ROOT
from frontend.api_client import APIClient
from frontend.cloud_runtime import (
    CloudBackend,
    CloudStartupError,
    _cached_backend,
    cloud_settings,
    ensure_backend,
)


@pytest.fixture(autouse=True)
def isolate_cloud_secrets(monkeypatch):
    # Never read a developer's actual Streamlit secrets during startup tests.
    monkeypatch.setattr("streamlit.secrets", SimpleNamespace(to_dict=lambda: {}))


@pytest.fixture
def cloud_config(tmp_path, monkeypatch):
    _cached_backend.clear()
    monkeypatch.setenv("OPENAI_API_KEY", "offline-cloud-test-key")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "cloud-data"))
    monkeypatch.setenv("BACKEND_URL", "http://127.0.0.1:1")
    yield cloud_settings({})
    _cached_backend.clear()


def test_settings_use_secrets_without_reading_the_local_dotenv(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    with pytest.raises(CloudStartupError, match="OPENAI_API_KEY"):
        cloud_settings({})
    config = cloud_settings({"OPENAI_API_KEY": "private-cloud-key", "OPENAI_MODEL": "configured-model"})
    assert config.data_dir == ROOT / "data" / "cloud"
    assert config.openai_api_key.get_secret_value() == "private-cloud-key"
    assert config.openai_model == "configured-model"
    assert config.data_dir != ROOT / "data"
    assert "private-cloud-key" not in repr(config)
    lists = cloud_settings({"OPENAI_API_KEY": "k", "PREVIEW_APPROVED_DOMAINS": "[]"})
    assert lists.preview_approved_domains == []


def test_invalid_secrets_do_not_echo_credentials(cloud_config):
    with pytest.raises(CloudStartupError) as error:
        cloud_settings({"OPENAI_API_KEY": "secret-never-echo", "MAX_REFERENCES": "invalid"})
    assert "secret-never-echo" not in str(error.value)


def test_cached_api_clients_follow_restarted_cloud_worker_but_explicit_urls_stay_fixed(monkeypatch):
    monkeypatch.setenv("BACKEND_URL", "http://127.0.0.1:9001/")
    automatic, fixed = APIClient(), APIClient("https://api.example.test/")
    assert automatic.base_url == "http://127.0.0.1:9001"
    monkeypatch.setenv("BACKEND_URL", "http://127.0.0.1:9002/")
    assert automatic.base_url == "http://127.0.0.1:9002"
    assert fixed.base_url == "https://api.example.test"


def test_real_cloud_backend_uses_a_private_port_and_preserves_data_after_cache_restart(cloud_config):
    backend = ensure_backend(cloud_config)
    with httpx.Client(base_url=backend.url, trust_env=False) as client:
        assert client.get("/health").json()["live_search_ready"]
        assert len(client.get("/source-categories").json()) == 8
        assert client.get("/_cloud/ready").json()["instance"] == backend.instance
        brief = client.post("/briefs", json={"concept": "Offline cloud startup check", "requested_count": 4})
        assert brief.status_code == 201
        brief_id = brief.json()["id"]
        pack = client.get("/library").json()["current_pack"]
    assert backend.url.startswith("http://127.0.0.1:")
    assert cloud_config.openai_api_key.get_secret_value() not in " ".join(backend.process.args)
    old_process = backend.process
    _cached_backend.clear()
    assert old_process.poll() is not None
    replacement = ensure_backend(cloud_config)
    assert replacement.process.pid != old_process.pid
    with httpx.Client(base_url=replacement.url, trust_env=False) as client:
        assert client.get(f"/briefs/{brief_id}").json()["concept"] == "Offline cloud startup check"
        assert client.get("/library").json()["current_pack"] == pack


def test_reruns_and_concurrent_sessions_share_one_worker(cloud_config):
    with ThreadPoolExecutor(max_workers=3) as executor:
        backends = list(executor.map(lambda _: ensure_backend(cloud_config), range(3)))
    assert all(backend is backends[0] for backend in backends)
    assert ensure_backend(cloud_config).process.pid == backends[0].process.pid


def test_configuration_changes_request_reboot_without_stopping_running_work(cloud_config):
    backend = ensure_backend(cloud_config)
    changed = cloud_config.model_copy(update={"openai_model": "another-configured-model"})
    with pytest.raises(CloudStartupError, match="Reboot"):
        ensure_backend(changed)
    assert backend.running()


def test_backend_code_changes_request_reboot_without_spawning_another_worker(cloud_config, monkeypatch):
    backend = ensure_backend(cloud_config)
    monkeypatch.setattr("frontend.cloud_runtime.backend_revision", lambda: "updated-code")
    with pytest.raises(CloudStartupError, match="Reboot"):
        ensure_backend(cloud_config)
    assert backend.running()


def test_failed_lifespan_startup_cleans_up_worker_and_shows_no_credentials(cloud_config, tmp_path):
    target = tmp_path / "not-a-directory"
    target.write_text("Offline startup failure fixture", encoding="utf-8")
    invalid = cloud_config.model_copy(update={"data_dir": target})
    with pytest.raises(CloudStartupError) as error:
        CloudBackend(invalid, startup_timeout=10)
    assert "offline-cloud-test-key" not in str(error.value)


def test_parent_pipe_closure_shuts_down_only_the_owned_api(cloud_config):
    with socket.socket() as unrelated:
        unrelated.bind(("127.0.0.1", 0))
        unrelated.listen()
        backend = CloudBackend(cloud_config)
        try:
            backend.process.stdin.close()
            backend.process.wait(timeout=12)
            assert not backend.running()
            # Closing the cloud backend never terminates other listeners.
            assert unrelated.getsockname()[1] > 0
        finally:
            backend.close()


def test_startup_failure_is_sanitized_and_does_not_change_local_backend(cloud_config, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("private-cloud-key must not reach the UI")

    monkeypatch.setattr("frontend.cloud_runtime.subprocess.Popen", fail)
    with pytest.raises(CloudStartupError) as error:
        CloudBackend(cloud_config)
    assert "private-cloud-key" not in str(error.value)


def test_actual_cloud_entry_renders_the_existing_ui_and_reuses_backend_on_rerun(cloud_config):
    entry = ROOT / "cloud" / "streamlit_app.py"
    first = AppTest.from_file(str(entry), default_timeout=30).run()
    assert not first.exception and not first.error
    assert [item.value for item in first.title] == ["Atomized Reference Finder"]
    assert any(item.label == "Describe your creative idea" for item in first.text_area)
    backend = ensure_backend(cloud_config)
    pid = backend.process.pid
    first.run()
    second = AppTest.from_file(str(entry), default_timeout=30).run()
    assert not first.exception and not second.exception and not second.error
    assert ensure_backend(cloud_config).process.pid == pid
    assert os.environ["BACKEND_URL"] == backend.url


def test_missing_cloud_key_shows_setup_instead_of_connecting_to_another_backend(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = AppTest.from_file(str(ROOT / "cloud" / "streamlit_app.py"), default_timeout=15).run()
    assert not app.exception
    assert any("OPENAI_API_KEY" in item.value and "Secrets" in item.value for item in app.error)
