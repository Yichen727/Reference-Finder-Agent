"""Safety checks for lifecycle commands; these tests never invoke search or OpenAI."""

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import run
from scripts import lifecycle
from scripts.lifecycle import Workspace, read_json, write_json
from scripts.processes import descendants, identity, same_process, service_role

PORTS = {"backend": 8000, "frontend": 8501}


def process(pid=101, ppid=1, created=100, command="python unrelated.py", exe="python.exe"):
    return dict(pid=pid, ppid=ppid, created=created, command=command, exe=exe)


def command(*tokens):
    if os.name == "nt":
        return subprocess.list2cmdline([str(token) for token in tokens])
    import shlex

    return shlex.join(str(token) for token in tokens)


def backend(root, pid=101, port=8000):
    return process(
        pid=pid,
        command=command("python", "-m", "uvicorn", "backend.main:app", "--app-dir", root, "--port", port),
    )


class FakeInspector:
    def __init__(self, processes=(), listeners=()):
        self.processes = {item["pid"]: item for item in processes}
        self.listeners = list(listeners)
        self.stopped = []

    def snapshot(self):
        return self.processes.copy(), self.listeners

    def running(self, saved):
        return same_process(self.processes.get(saved["pid"]), saved)

    def stop(self, saved):
        assert self.running(saved)
        self.stopped.append(saved["pid"])
        del self.processes[saved["pid"]]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "Inspector", lambda *args: FakeInspector())
    monkeypatch.setattr(lifecycle, "available_port", lambda port: None)
    return Workspace(tmp_path)


def save_state(workspace, supervisor):
    state = dict(
        root=str(workspace.root),
        ports=workspace.ports,
        instance="test-instance",
        supervisor=identity(supervisor),
        workers=[],
    )
    workspace.state_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(workspace.state_path, state)
    return state


def test_absolute_paths_prove_workspace_ownership(tmp_path):
    # Spaces in project paths must survive Windows command-line parsing.
    root = tmp_path / "Project With Spaces"
    worker = backend(root)
    assert service_role(worker, {worker["pid"]: worker}, root, PORTS) == "backend"
    worker["command"] = command(
        "python", "-m", "streamlit", "run", root / "frontend/app.py", "--server.port", 8501
    )
    assert service_role(worker, {}, root, PORTS) == "frontend"


def test_other_project_and_other_port_are_not_owned(tmp_path):
    own_python = str(tmp_path / ".venv/Scripts/python.exe")
    worker = backend(tmp_path / "different")
    worker["exe"] = own_python
    assert service_role(worker, {}, tmp_path, PORTS) is None
    assert service_role(backend(tmp_path, port=8001), {}, tmp_path, PORTS) is None


def test_legacy_venv_redirector_is_recognised(tmp_path):
    worker = process(command="python -m uvicorn backend.main:app --port 8000", ppid=100, created=101)
    parent = process(pid=100, created=100, exe=str(tmp_path / ".venv/Scripts/python.exe"))
    assert service_role(worker, {100: parent}, tmp_path, PORTS) == "backend"
    parent["created"] = 102  # Its PID was reused after the child started.
    assert service_role(worker, {100: parent}, tmp_path, PORTS) is None


@pytest.mark.parametrize(
    "text",
    [
        "python",
        "python -m",
        'python -m "',
        "python -m uvicorn backend.main:app --app-dir",
        "python -m uvicorn backend.main:app --port",
        "python unrelated.py -m uvicorn backend.main:app --app-dir anywhere --port 8000",
    ],
)
def test_malformed_commands_do_not_authorise_cleanup(tmp_path, text):
    assert service_role(process(command=text), {}, tmp_path, PORTS) is None


def test_pid_reuse_and_command_changes_are_rejected():
    worker = process()
    saved = identity(worker)
    assert same_process(worker, saved)
    assert not same_process({**worker, "created": 101}, saved)
    assert not same_process({**worker, "command": "python other.py"}, saved)
    assert not same_process({**worker, "exe": "other.exe"}, saved)


def test_descendants_are_child_first_and_ignore_parent_pid_reuse():
    parent = process(pid=10, created=100)
    child = process(pid=11, ppid=10, created=101)
    stale = process(pid=12, ppid=10, created=99)
    assert [item["pid"] for item in descendants(parent, {10: parent, 11: child, 12: stale})] == [11, 10]


def test_foreign_frontend_prevents_all_cleanup(workspace):
    worker = backend(workspace.root)
    foreign = process(pid=102)
    workspace.inspector = FakeInspector(
        [worker, foreign], [{"pid": 101, "port": 8000}, {"pid": 102, "port": 8501}]
    )
    with pytest.raises(RuntimeError, match="Port 8501 belongs to another application"):
        workspace.stop()
    assert workspace.inspector.stopped == []


def test_unknown_port_owner_is_not_terminated(workspace):
    workspace.inspector = FakeInspector(listeners=[{"pid": None, "port": 8000}])
    with pytest.raises(RuntimeError, match="unverified owner"):
        workspace.stop()


def test_owned_orphan_cleanup_preserves_unrelated_python(workspace):
    worker = backend(workspace.root)
    child = process(pid=102, ppid=101, created=101)
    foreign = process(pid=103)
    workspace.inspector = FakeInspector([worker, child, foreign])
    workspace.stop()
    assert workspace.inspector.stopped == [102, 101]
    assert workspace.inspector.running(identity(foreign))


def test_stale_supervisor_record_does_not_kill_reused_pid(workspace):
    previous = process(command=command("python", workspace.root / "run.py"))
    save_state(workspace, previous)
    replacement = {**previous, "created": 101}
    workspace.inspector = FakeInspector([replacement])
    workspace.stop()
    assert workspace.inspector.stopped == []
    assert not workspace.state_path.exists()


def test_record_cannot_authorise_an_unrelated_launcher(workspace):
    foreign = process()
    save_state(workspace, foreign)
    workspace.inspector = FakeInspector([foreign])
    workspace.stop()
    assert workspace.inspector.stopped == []


@pytest.mark.parametrize("value", [None, [], {"root": "elsewhere"}, {"supervisor": 123}])
def test_invalid_state_is_ignored(workspace, value):
    workspace.state_path.parent.mkdir(parents=True)
    write_json(workspace.state_path, value)
    assert workspace.saved_state() is None
    workspace.stop()


def test_status_is_read_only(workspace):
    worker = backend(workspace.root)
    workspace.inspector = FakeInspector([worker], [{"pid": 101, "port": 8000}])
    assert workspace.status() == "Backend: running (PID 101); port 8000\nFrontend: stopped; port 8501"
    assert not workspace.state_path.parent.exists()
    assert workspace.inspector.stopped == []


def test_stop_is_idempotent_and_preserves_research(workspace):
    research = workspace.root / "data/reference_finder.sqlite3"
    research.parent.mkdir(parents=True)
    research.write_bytes(b"saved research")
    workspace.stop()
    workspace.stop()
    assert research.read_bytes() == b"saved research"


def test_stop_request_only_matches_its_instance(workspace):
    workspace.record = {"instance": "current"}
    workspace.stop_path.parent.mkdir(parents=True)
    write_json(workspace.stop_path, {"instance": "older"})
    assert not workspace.stopped()
    write_json(workspace.stop_path, {"instance": "current"})
    assert workspace.stopped()


@pytest.mark.parametrize("ports", [(0, 8501), (8000, 65536), (8501, 8501)])
def test_invalid_ports_fail_before_inspection(tmp_path, ports):
    with pytest.raises(ValueError):
        Workspace(tmp_path, *ports)


@pytest.mark.parametrize("action", [[], ["stop"], ["restart"], ["status"]])
def test_cli_commands_dispatch_and_close(monkeypatch, action):
    calls = []
    fake = SimpleNamespace(
        url="http://localhost:8501",
        backend_url="http://localhost:8000",
        start=lambda python: calls.append("start"),
        stop=lambda: calls.append("stop"),
        status=lambda: calls.append("status"),
        wait=lambda: calls.append("wait"),
        close=lambda: calls.append("close"),
    )
    monkeypatch.setattr(run, "Workspace", lambda *args: fake)
    monkeypatch.setattr(run, "open_workspace", lambda url: calls.append("browser"))
    assert run.main(["--open-browser", *action]) == 0
    expected = (
        ["stop", "close"]
        if action == ["stop"]
        else (["status", "close"] if action == ["status"] else ["start", "browser", "wait", "close"])
    )
    assert calls == expected


@pytest.mark.parametrize("error", [KeyboardInterrupt(), RuntimeError("worker startup failed")])
def test_cli_always_cleans_up_after_interruption_or_partial_start(monkeypatch, error):
    calls = []

    def fail(python):
        raise error

    monkeypatch.setattr(
        run, "Workspace", lambda *args: SimpleNamespace(start=fail, close=lambda: calls.append("close"))
    )
    assert run.main([]) == (0 if isinstance(error, KeyboardInterrupt) else 1)
    assert calls == ["close"]


def test_lock_released_when_startup_fails(tmp_path):
    lock = tmp_path / "lifecycle.lock"
    with pytest.raises(RuntimeError), lifecycle.lifecycle_lock(lock):
        raise RuntimeError("startup failed")
    with lifecycle.lifecycle_lock(lock):
        pass


def test_concurrent_lifecycle_command_cannot_acquire_lock(tmp_path, monkeypatch):
    with lifecycle.lifecycle_lock(tmp_path / "lifecycle.lock"):
        clock = iter([0, 36])
        monkeypatch.setattr(lifecycle.time, "monotonic", lambda: next(clock))
        with pytest.raises(RuntimeError, match="Another lifecycle command is still running"):
            with lifecycle.lifecycle_lock(tmp_path / "lifecycle.lock"):
                pytest.fail("A second command acquired the lock")


def test_corrupt_json_is_ignored(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{unfinished", encoding="utf-8")
    assert read_json(path) is None


def test_windows_inspection_failure_has_actionable_message(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows inspection only")
    from scripts.processes import Inspector

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "powershell")),
    )
    with pytest.raises(RuntimeError, match="check local permissions"):
        Inspector(Path(tmp_path), PORTS).snapshot()
