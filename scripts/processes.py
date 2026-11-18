"""Read process identities and port ownership before any lifecycle mutation."""

import hashlib
import json
import os
import shlex
import signal
import subprocess
from pathlib import Path


def identity(process):
    return {
        "pid": int(process["pid"]),
        "created": process["created"],
        "exe": process["exe"],
        "command_hash": hashlib.sha256(process["command"].encode("utf-8")).hexdigest(),
    }


def same_process(process, saved):
    return bool(process and saved and identity(process) == {key: saved.get(key) for key in identity(process)})


def path_key(value):
    value = str(value).replace("\\", "/").rstrip("/")
    return value.casefold() if os.name == "nt" else value


def service_role(process, processes, root, ports):
    """Require an absolute application path or a matching workspace venv ancestor."""
    try:
        tokens = [token.strip('"') for token in shlex.split(process["command"], posix=os.name != "nt")]
    except (ValueError, KeyError):
        return None
    if len(tokens) < 4 or tokens[1] != "-m":
        return None
    marker = 1
    module = tokens[marker + 1 : marker + 3]
    if module == ["uvicorn", "backend.main:app"]:
        role, flag = "backend", "--port"
        absolute = False
        if "--app-dir" in tokens:
            index = tokens.index("--app-dir") + 1
            if index >= len(tokens) or path_key(tokens[index]) != path_key(root):
                return None
            absolute = True
    elif module == ["streamlit", "run"] and len(tokens) > marker + 3:
        role, flag = "frontend", "--server.port"
        script = tokens[marker + 3]
        absolute = path_key(script) == path_key(root / "frontend/app.py")
        if not absolute and path_key(script) != "frontend/app.py":
            return None
    else:
        return None
    try:
        if int(tokens[tokens.index(flag) + 1]) != ports[role]:
            return None
    except (ValueError, IndexError):
        return None
    if absolute:
        return role
    expected = path_key(root / ".venv/Scripts/python.exe")
    ancestor = process
    for _ in range(8):
        if path_key(ancestor.get("exe", "")) == expected:
            return role
        parent = processes.get(ancestor.get("ppid"))
        if not parent or parent["created"] > ancestor["created"]:
            break
        ancestor = parent
    return None


def descendants(process, processes, visited=None):
    """Return children first; reject parent-PID reuse across process generations."""
    visited = set() if visited is None else visited
    if process["pid"] in visited:
        return []
    visited.add(process["pid"])
    result = []
    for child in processes.values():
        if child["ppid"] == process["pid"] and child["pid"] != process["pid"]:
            if child["created"] >= process["created"]:
                result.extend(descendants(child, processes, visited))
    result.append(process)
    return result


class Inspector:
    def __init__(self, root, ports):
        self.root, self.ports = root, ports
        if os.name == "nt":
            from scripts.windows_process import WindowsAPI

            self.native = WindowsAPI()
        elif not Path("/proc/net/tcp").exists():
            raise RuntimeError("Process ownership inspection requires Windows or Linux /proc.")

    def snapshot(self):
        if os.name == "nt":
            try:
                response = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-NonInteractive",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(self.root / "scripts/process_snapshot.ps1"),
                        "-BackendPort",
                        str(self.ports["backend"]),
                        "-FrontendPort",
                        str(self.ports["frontend"]),
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8-sig",
                    errors="replace",
                    timeout=20,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    check=True,
                )
                data = json.loads(response.stdout)
                return {p["pid"]: p for p in data["processes"]}, data["listeners"]
            except (subprocess.SubprocessError, ValueError, KeyError) as error:
                raise RuntimeError(
                    "Could not inspect Windows processes and ports; check local permissions."
                ) from error
        return self.linux_snapshot()

    def linux_process(self, folder):
        stat = (folder / "stat").read_text().rsplit(")", 1)[1].split()
        return {
            "pid": int(folder.name),
            "ppid": int(stat[1]),
            "created": int(stat[19]),
            "exe": str((folder / "exe").resolve(strict=True)),
            "command": shlex.join((folder / "cmdline").read_bytes().decode().strip("\0").split("\0")),
        }

    def linux_snapshot(self):
        processes, socket_ports = {}, {}
        for name in ("tcp", "tcp6"):
            for row in Path(f"/proc/net/{name}").read_text().splitlines()[1:]:
                fields = row.split()
                port = int(fields[1].split(":")[1], 16)
                if fields[3] == "0A" and port in self.ports.values():
                    socket_ports[fields[9]] = port
        owners = {}
        for folder in Path("/proc").glob("[0-9]*"):
            try:
                process = self.linux_process(folder)
                processes[process["pid"]] = process
                for descriptor in (folder / "fd").iterdir():
                    try:
                        link = os.readlink(descriptor)
                    except OSError:
                        continue
                    if link.startswith("socket:[") and link[8:-1] in socket_ports:
                        owners[link[8:-1]] = process["pid"]
            except (OSError, ValueError, IndexError):
                continue
        listeners = [{"port": port, "pid": owners.get(inode)} for inode, port in socket_ports.items()]
        return processes, listeners

    def running(self, saved):
        if os.name == "nt":
            return self.native.running(saved)
        try:
            return same_process(self.linux_process(Path(f"/proc/{saved['pid']}")), saved)
        except (OSError, ValueError, IndexError):
            return False

    def stop(self, saved):
        if os.name == "nt":
            self.native.stop(saved)
        elif self.running(saved):
            os.kill(saved["pid"], signal.SIGTERM)
