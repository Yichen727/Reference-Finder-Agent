"""Private API worker for the cloud entrypoint; configuration arrives through stdin."""

import json
import socket
import sys
import threading

import uvicorn

from backend.core.config import Settings
from backend.main import create_app


def serve():
    payload = json.loads(sys.stdin.readline())
    settings = Settings(_env_file=None, **payload["settings"])
    instance = payload["instance"]
    app = create_app(settings)

    @app.get("/_cloud/ready", include_in_schema=False)
    async def ready():
        return {"status": "ok", "instance": instance}

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", workers=1, access_log=False,
            log_level="warning", timeout_graceful_shutdown=5,
        ))

        def watch_parent():
            # EOF also detects an abruptly closed parent, without guessing any PIDs.
            sys.stdin.read()
            server.should_exit = True

        threading.Thread(target=watch_parent, name="cloud-parent-watch", daemon=True).start()
        print(json.dumps({"port": listener.getsockname()[1], "instance": instance}), flush=True)
        server.run(sockets=[listener])


if __name__ == "__main__":
    try:
        serve()
    except Exception as error:
        # Never put settings, credentials or exception bodies into startup logs.
        print(f"Cloud API startup failed ({type(error).__name__}).", file=sys.stderr, flush=True)
        raise SystemExit(1) from None
