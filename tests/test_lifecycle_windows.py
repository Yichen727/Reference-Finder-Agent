"""Real Windows process-tree tests using isolated fixtures and ephemeral ports."""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

import pytest

from scripts.lifecycle import read_json
from scripts.processes import Inspector

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows process lifecycle integration")
ROOT = Path(__file__).resolve().parents[1]


def wait_until(check, timeout=50):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.15)
    raise AssertionError("Lifecycle condition did not complete before its timeout")


def free_ports():
    with socket.socket() as first, socket.socket() as second:
        first.bind(("127.0.0.1", 0))
        second.bind(("127.0.0.1", 0))
        return first.getsockname()[1], second.getsockname()[1]


def port_free(port):
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


class FixtureWorkspace:
    def __init__(self, root):
        self.root = root / "Workspace With Spaces"
        self.root.mkdir()
        shutil.copy2(ROOT / "run.py", self.root / "run.py")
        (self.root / "scripts").mkdir()
        for name in (
            "__init__.py",
            "lifecycle.py",
            "processes.py",
            "windows_process.py",
            "process_snapshot.ps1",
        ):
            shutil.copy2(ROOT / "scripts" / name, self.root / "scripts" / name)
        (self.root / "backend").mkdir()
        (self.root / "backend/__init__.py").touch()
        (self.root / "backend/main.py").write_text(
            'from fastapi import FastAPI\napp = FastAPI()\n@app.get("/health")\n'
            'def health():\n    return {"status": "ok"}\n',
            encoding="utf-8",
        )
        (self.root / "frontend").mkdir()
        (self.root / "frontend/app.py").write_text(
            'import streamlit as st\nst.title("Lifecycle fixture")\n', encoding="utf-8"
        )
        backend, frontend = free_ports()
        self.ports = dict(backend=backend, frontend=frontend)
        self.arguments = ["--backend-port", str(backend), "--frontend-port", str(frontend)]
        self.state_path = self.root / f"data/runtime/workspace-{backend}-{frontend}.json"
        self.inspector = Inspector(self.root, self.ports)
        self.launchers, self.handles = [], []

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(self.root / "run.py"), *args, *self.arguments],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=55,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def start(self, action="start", console=False):
        path = self.root / f"launcher-{len(self.launchers)}.log"
        handle = path.open("w", encoding="utf-8")
        self.handles.append(handle)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        process = subprocess.Popen(
            [sys.executable, str(self.root / "run.py"), action, *self.arguments],
            cwd=self.root,
            stdout=handle,
            stderr=subprocess.STDOUT,
            startupinfo=startup,
            creationflags=subprocess.CREATE_NEW_CONSOLE if console else subprocess.CREATE_NO_WINDOW,
        )
        self.launchers.append(process)

        def ready():
            output = path.read_text(encoding="utf-8", errors="replace")
            if process.poll() is not None:
                raise AssertionError(f"Launcher exited early:\n{output}")
            return "Workspace: http" in output

        wait_until(ready)
        return process, path

    def assert_released(self, record):
        wait_until(lambda: all(port_free(port) for port in self.ports.values()), timeout=15)
        assert all(not self.inspector.running(worker) for worker in record["workers"])

    def cleanup(self):
        # Only test-owned identities and their Windows jobs may be stopped here.
        state = read_json(self.state_path)
        if state:
            supervisor = state.get("supervisor")
            if supervisor and self.inspector.running(supervisor):
                self.inspector.stop(supervisor)
        for launcher in self.launchers:
            if launcher.poll() is None:
                launcher.kill()
            launcher.wait(timeout=10)
        for handle in self.handles:
            handle.close()


@pytest.fixture
def app(tmp_path):
    fixture = FixtureWorkspace(tmp_path)
    try:
        yield fixture
    finally:
        fixture.cleanup()


def test_start_status_restart_stop_and_idempotent_stop(app):
    first, _ = app.start()
    initial = read_json(app.state_path)
    assert len(initial["workers"]) == 2
    assert all(app.inspector.running(item) for item in initial["workers"])
    status = app.cli("status")
    assert status.returncode == 0, status.stderr
    assert "Backend: running" in status.stdout and "Frontend: running" in status.stdout
    second, _ = app.start("restart")
    first.wait(timeout=10)
    assert first.returncode == 0
    current = read_json(app.state_path)
    assert current["instance"] != initial["instance"]
    assert all(not app.inspector.running(item) for item in initial["workers"])
    stopped = app.cli("stop")
    assert stopped.returncode == 0, stopped.stderr
    second.wait(timeout=10)
    assert second.returncode == 0
    app.assert_released(current)
    assert not app.state_path.exists()
    assert app.cli("stop").returncode == 0
    assert app.cli("status").stdout.count("stopped") == 2


def test_abrupt_supervisor_exit_kills_workers_and_allows_next_start(app):
    first, _ = app.start()
    state = read_json(app.state_path)
    # Killing the actual Python supervisor also tests the venv redirector case.
    app.inspector.stop(state["supervisor"])
    first.wait(timeout=10)
    app.assert_released(state)
    assert app.state_path.exists()  # Abrupt exit leaves a stale record.
    second, _ = app.start()
    current = read_json(app.state_path)
    assert current["instance"] != state["instance"]
    assert app.cli("stop").returncode == 0
    second.wait(timeout=10)
    app.assert_released(current)


def test_foreign_port_is_reported_and_other_application_survives(app):
    port = app.ports["backend"]
    foreign = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        wait_until(lambda: not port_free(port))
        status = app.cli("status")
        assert "occupied by another application" in status.stdout
        for action in ("start", "restart", "stop"):
            result = app.cli(action)
            assert result.returncode == 1
            assert f"Port {port} belongs to another application" in result.stderr
            assert foreign.poll() is None
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/", timeout=2) as response:
            assert response.status == 200
    finally:
        # http.server is launched by this test; stop its actual interpreter as well.
        snapshot, _ = app.inspector.snapshot()
        from scripts.processes import descendants, identity

        if foreign.pid in snapshot:
            for item in descendants(snapshot[foreign.pid], snapshot):
                if app.inspector.running(identity(item)):
                    app.inspector.stop(identity(item))
        foreign.wait(timeout=10)


def test_partial_start_failure_releases_backend(app):
    (app.root / "frontend/app.py").unlink()
    result = app.cli("start")
    assert result.returncode == 1
    assert "frontend" in result.stderr.lower()
    assert not app.state_path.exists()
    assert all(port_free(port) for port in app.ports.values())


def test_ctrl_c_in_own_console_gracefully_stops_both_services(app):
    launcher, log = app.start(console=True)
    state = read_json(app.state_path)
    # A separate helper attaches only to the test's hidden console. CTRL_C_EVENT
    # cannot escape into VS Code's or pytest's console.
    helper = r"""
import ctypes, sys
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.FreeConsole()
if not kernel.AttachConsole(int(sys.argv[1])):
    raise ctypes.WinError(ctypes.get_last_error())
kernel.SetConsoleCtrlHandler(None, True)
if not kernel.GenerateConsoleCtrlEvent(0, 0):
    raise ctypes.WinError(ctypes.get_last_error())
"""
    subprocess.run(
        [sys.executable, "-c", helper, str(launcher.pid)],
        check=True,
        timeout=5,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    launcher.wait(timeout=15)
    assert launcher.returncode == 0, log.read_text(encoding="utf-8")
    output = log.read_text(encoding="utf-8")
    assert "Stopping the workspace" in output
    assert "Application shutdown complete" in output
    app.assert_released(state)
    assert not app.state_path.exists()


def test_untracked_owned_backend_is_recovered(app):
    backend = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.main:app",
            "--app-dir",
            str(app.root),
            "--host",
            "127.0.0.1",
            "--port",
            str(app.ports["backend"]),
        ],
        cwd=app.root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        wait_until(lambda: not port_free(app.ports["backend"]))
        assert not app.state_path.exists()
        launcher, _ = app.start()
        backend.wait(timeout=10)
        current = read_json(app.state_path)
        assert app.cli("stop").returncode == 0
        launcher.wait(timeout=10)
        app.assert_released(current)
    finally:
        if backend.poll() is None:
            snapshot, _ = app.inspector.snapshot()
            from scripts.processes import descendants, identity

            if backend.pid in snapshot:
                for item in descendants(snapshot[backend.pid], snapshot):
                    if app.inspector.running(identity(item)):
                        app.inspector.stop(identity(item))
        backend.wait(timeout=10)
