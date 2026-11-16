"""Run the local backend and frontend with the active Python environment."""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent


def available_port(port: int):
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            raise RuntimeError(
                f"Port {port} is unavailable. Stop the existing service or choose another port."
            ) from None


def wait_for_service(opener, url: str, process: subprocess.Popen, label: str):
    deadline = time.monotonic() + 25
    while True:
        if process.poll() is not None:
            raise RuntimeError(f"{label} stopped during startup. Check its output above.")
        try:
            with opener.open(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            pass
        if time.monotonic() > deadline:
            raise RuntimeError(f"{label} did not become ready within 25 seconds.")
        time.sleep(0.25)


def open_workspace(url: str):
    try:
        if not webbrowser.open(url):
            print(f"Open {url} in your browser.", flush=True)
    except OSError:
        print(f"Could not open a browser. Open {url} manually.", flush=True)


def workspace_running(opener, backend_url: str, frontend_url: str) -> bool:
    try:
        with opener.open(backend_url + "/health", timeout=1) as response:
            health = json.load(response)
        if not (
            health.get("status") == "ok"
            and health.get("mode") == "live"
            and "live_search" in health.get("capabilities", {})
            and health.get("search_provider") == "openai"
        ):
            return False
        with opener.open(frontend_url + "/_stcore/health", timeout=1) as response:
            return response.status == 200 and response.read().strip() == b"ok"
    except (OSError, ValueError, AttributeError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--frontend-port", type=int, default=8501)
    parser.add_argument("--open-browser", action="store_true", help="Open the workspace when it is ready")
    args = parser.parse_args()
    processes = []
    try:
        if not all(1 <= port <= 65535 for port in [args.backend_port, args.frontend_port]):
            raise RuntimeError("Ports must be between 1 and 65535.")
        if args.backend_port == args.frontend_port:
            raise RuntimeError("Backend and frontend need different ports.")
        backend_url = f"http://127.0.0.1:{args.backend_port}"
        workspace_url = f"http://127.0.0.1:{args.frontend_port}"
        opener = build_opener(ProxyHandler({}))
        if args.open_browser and workspace_running(opener, backend_url, workspace_url):
            print(f"Workspace is already running: {workspace_url}", flush=True)
            open_workspace(workspace_url)
            return 0
        available_port(args.backend_port)
        available_port(args.frontend_port)
        environment = os.environ.copy()
        environment["BACKEND_URL"] = backend_url
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        backend = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "backend.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.backend_port),
                "--workers",
                "1",
            ],
            cwd=ROOT,
            env=environment,
            creationflags=creation_flags,
        )
        processes.append(backend)
        wait_for_service(opener, environment["BACKEND_URL"] + "/health", backend, "Backend")
        frontend = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                "frontend/app.py",
                "--server.address",
                "127.0.0.1",
                "--server.port",
                str(args.frontend_port),
                "--server.headless",
                "true",
            ],
            cwd=ROOT,
            env=environment,
            creationflags=creation_flags,
        )
        processes.append(frontend)
        wait_for_service(opener, workspace_url + "/_stcore/health", frontend, "Frontend")
        print(f"\nWorkspace: {workspace_url}", flush=True)
        print(f"API docs:  {environment['BACKEND_URL']}/docs", flush=True)
        print("Press Ctrl+C to stop both services.\n", flush=True)
        if args.open_browser:
            open_workspace(workspace_url)
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        raise RuntimeError("A service stopped. Check its output above.")
    except KeyboardInterrupt:
        print("\nStopping the workspace...", flush=True)
    except (RuntimeError, OSError) as error:
        print(f"Startup error: {error}", file=sys.stderr)
        return 1
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
