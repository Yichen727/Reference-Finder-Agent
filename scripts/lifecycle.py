"""PID records, exclusive startup and coordinated service shutdown."""

import json
import os
import shlex
import signal
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

from scripts.processes import Inspector, descendants, identity, same_process, service_role


@contextmanager
def lifecycle_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + 35
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Another lifecycle command is still running. Please retry.") from None
                time.sleep(0.1)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def write_json(path, value):
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(value), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def available_port(port):
    with socket.socket() as probe:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            raise RuntimeError(f"Port {port} is unavailable; it was not possible to reserve it.") from None


class Workspace:
    def __init__(self, root: Path, backend_port=8000, frontend_port=8501):
        if not all(1 <= port <= 65535 for port in (backend_port, frontend_port)):
            raise ValueError("Ports must be between 1 and 65535.")
        if backend_port == frontend_port:
            raise ValueError("Backend and frontend need different ports.")
        self.root = root.resolve()
        self.ports = {"backend": backend_port, "frontend": frontend_port}
        self.backend_url = f"http://127.0.0.1:{backend_port}"
        self.url = f"http://127.0.0.1:{frontend_port}"
        folder = self.root / "data" / "runtime"
        self.state_path = folder / f"workspace-{backend_port}-{frontend_port}.json"
        self.stop_path = self.state_path.with_suffix(".stop")
        self.lock_path = self.state_path.with_suffix(".lock")
        self.inspector = Inspector(self.root, self.ports)
        self.processes, self.job, self.record = [], None, None
        self.opener = build_opener(ProxyHandler({}))

    def saved_state(self):
        state = read_json(self.state_path)
        if not state or state.get("root") != str(self.root) or state.get("ports") != self.ports:
            return None
        supervisor = state.get("supervisor")
        if not isinstance(supervisor, dict) or not isinstance(state.get("instance"), str):
            return None
        if not all(
            isinstance(supervisor.get(key), int) and supervisor[key] > 0 for key in ("pid", "created")
        ):
            return None
        if not all(
            isinstance(supervisor.get(key), str) and supervisor[key] for key in ("exe", "command_hash")
        ):
            return None
        return state

    def active_supervisor(self, state, processes):
        if not state:
            return None
        saved = state["supervisor"]
        process = processes.get(saved["pid"])
        if not same_process(process, saved):
            return None
        try:
            tokens = [token.strip('"') for token in shlex.split(process["command"], posix=os.name != "nt")]
        except ValueError:
            return None
        from scripts.processes import path_key

        if len(tokens) < 2 or path_key(tokens[1]) not in {"run.py", path_key(self.root / "run.py")}:
            return None
        return saved

    def inspect(self):
        processes, listeners = self.inspector.snapshot()
        services = {
            pid: service_role(process, processes, self.root, self.ports) for pid, process in processes.items()
        }
        return processes, listeners, services

    def require_owned_ports(self, processes, listeners, services):
        for listener in listeners:
            if not services.get(listener["pid"]):
                owner = f"PID {listener['pid']}" if listener["pid"] in processes else "unverified owner"
                raise RuntimeError(
                    f"Port {listener['port']} belongs to another application ({owner}); it was left running."
                )

    def status(self):
        processes, listeners, services = self.inspect()
        state = self.saved_state()
        active = self.active_supervisor(state, processes) is not None
        lines = []
        for role, port in self.ports.items():
            owners = {item["pid"] for item in listeners if item["port"] == port}
            if not owners:
                label = "starting or not listening" if active else "stopped"
            else:
                label = (
                    "running"
                    if all(services.get(pid) for pid in owners)
                    else "occupied by another application"
                )
                label += " (PID " + ", ".join(str(pid) for pid in sorted(owners, key=str)) + ")"
            lines.append(f"{role.capitalize()}: {label}; port {port}")
        return "\n".join(lines)

    def cleanup_existing(self):
        processes, listeners, services = self.inspect()
        # Validate both ports before stopping any process.
        self.require_owned_ports(processes, listeners, services)
        state = self.saved_state()
        supervisor = self.active_supervisor(state, processes)
        if supervisor:
            if supervisor["pid"] == os.getpid():
                raise RuntimeError("The PID record points to this lifecycle command; cleanup was cancelled.")
            write_json(self.stop_path, {"instance": state["instance"]})
            deadline = time.monotonic() + 8
            while self.inspector.running(supervisor) and time.monotonic() < deadline:
                time.sleep(0.1)
            if self.inspector.running(supervisor):
                self.inspector.stop(supervisor)
        targets = {}
        for pid, role in services.items():
            if role:
                for process in descendants(processes[pid], processes):
                    targets.setdefault(process["pid"], identity(process))
        for saved in targets.values():
            if saved["pid"] != os.getpid() and self.inspector.running(saved):
                self.inspector.stop(saved)
        deadline = time.monotonic() + 8
        while True:
            try:
                for port in self.ports.values():
                    available_port(port)
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "A workspace port did not close. Check status before retrying."
                    ) from None
                time.sleep(0.1)
        self.state_path.unlink(missing_ok=True)
        self.stop_path.unlink(missing_ok=True)

    def stop(self):
        with lifecycle_lock(self.lock_path):
            self.cleanup_existing()

    def stopped(self):
        message = read_json(self.stop_path)
        return bool(message and self.record and message.get("instance") == self.record["instance"])

    def wait_for_service(self, process, url, role):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"{role.capitalize()} exited during startup. Check its output above.")
            try:
                with self.opener.open(url, timeout=1) as response:
                    if response.status == 200:
                        return
            except OSError:
                pass
            time.sleep(0.2)
        raise RuntimeError(f"{role.capitalize()} did not become ready within 25 seconds.")

    def start(self, python):
        with lifecycle_lock(self.lock_path):
            self.cleanup_existing()
            processes, _, _ = self.inspect()
            supervisor = processes.get(os.getpid())
            if supervisor is None:
                raise RuntimeError("Could not record the launcher's process identity.")
            self.record = {
                "version": 1,
                "root": str(self.root),
                "ports": self.ports,
                "instance": str(uuid4()),
                "supervisor": identity(supervisor),
                "workers": [],
            }
            write_json(self.state_path, self.record)
            if os.name == "nt":
                from scripts.windows_process import WindowsJob

                self.job = WindowsJob()
            commands = {
                "backend": [
                    python,
                    "-m",
                    "uvicorn",
                    "backend.main:app",
                    "--app-dir",
                    str(self.root),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(self.ports["backend"]),
                    "--workers",
                    "1",
                ],
                "frontend": [
                    python,
                    "-m",
                    "streamlit",
                    "run",
                    str(self.root / "frontend/app.py"),
                    "--server.address",
                    "127.0.0.1",
                    "--server.port",
                    str(self.ports["frontend"]),
                    "--server.headless",
                    "true",
                ],
            }
            for role, command in commands.items():
                kwargs = {"cwd": self.root, "env": {**os.environ, "BACKEND_URL": self.backend_url}}
                process = (
                    self.job.spawn(command, **kwargs)
                    if self.job
                    else subprocess.Popen(command, start_new_session=True, **kwargs)
                )
                self.processes.append(process)
                snapshot, _, _ = self.inspect()
                if process.pid not in snapshot:
                    raise RuntimeError(f"Could not record the {role} PID; startup was cancelled.")
                self.record["workers"].append({**identity(snapshot[process.pid]), "role": role})
                write_json(self.state_path, self.record)
                health = self.backend_url + "/health" if role == "backend" else self.url + "/_stcore/health"
                self.wait_for_service(process, health, role)

    def wait(self):
        while not self.stopped():
            if any(process.poll() is not None for process in self.processes):
                raise RuntimeError("A workspace service stopped unexpectedly. Both services will be stopped.")
            time.sleep(0.2)

    def close(self):
        for process in self.processes:
            if process.poll() is None:
                try:
                    if os.name == "nt":
                        process.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        os.killpg(process.pid, signal.SIGTERM)
                except OSError:
                    pass
        deadline = time.monotonic() + 5
        for process in self.processes:
            try:
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        if self.job:
            self.job.close()
        for process in self.processes:
            if process.poll() is None:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait(timeout=5)
        if self.record:
            current = self.saved_state()
            if current and current.get("instance") == self.record["instance"]:
                self.state_path.unlink(missing_ok=True)
                self.stop_path.unlink(missing_ok=True)
