"""Start, stop, restart or inspect the local Reference Finder workspace."""

import argparse
import signal
import subprocess
import sys
import webbrowser
from pathlib import Path

from scripts.lifecycle import Workspace

ROOT = Path(__file__).resolve().parent


def open_workspace(url):
    try:
        if not webbrowser.open(url):
            print(f"Open {url} in your browser.", flush=True)
    except OSError:
        print(f"Could not open a browser. Open {url} manually.", flush=True)


def interrupt(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=["start", "stop", "restart", "status"], default="start")
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--frontend-port", type=int, default=8501)
    parser.add_argument("--open-browser", action="store_true", help="Open the workspace when it is ready")
    args = parser.parse_args(argv)
    workspace = None
    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        workspace = Workspace(ROOT, args.backend_port, args.frontend_port)
        if args.action == "status":
            print(workspace.status(), flush=True)
        elif args.action == "stop":
            workspace.stop()
            print("Workspace stopped. Saved research and exports are unchanged.", flush=True)
        else:
            workspace.start(sys.executable)
            print(f"\nWorkspace: {workspace.url}", flush=True)
            print(f"API docs:  {workspace.backend_url}/docs", flush=True)
            print("Press Ctrl+C to stop both services, or use python run.py stop.\n", flush=True)
            if args.open_browser:
                open_workspace(workspace.url)
            workspace.wait()
    except KeyboardInterrupt:
        print("\nStopping the workspace...", flush=True)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Workspace error: {error}", file=sys.stderr)
        return 1
    finally:
        try:
            # Repeated Ctrl+C must not interrupt coordinated cleanup.
            previous_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
            if workspace:
                workspace.close()
        except (RuntimeError, OSError, subprocess.SubprocessError) as error:
            print(f"Shutdown error: {error}. Run python run.py status before retrying.", file=sys.stderr)
            return 1
        finally:
            signal.signal(signal.SIGINT, previous_int)
            signal.signal(signal.SIGTERM, previous)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
