"""Chrome acceptance check with isolated test records; no OpenAI calls or user-data writes."""

import base64
import json
import os
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from docx import Document  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from websockets.sync.client import connect  # noqa: E402

from backend.core.config import Settings  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.repositories.sqlite import Repository  # noqa: E402
from backend.schemas.models import ApprovedThumbnail  # noqa: E402
from tests.conftest import completed_run  # noqa: E402
from tests.fixtures import FixtureProvider  # noqa: E402

ARTIFACTS = ROOT / "data" / "ui-review" / str(int(time.time()))
OPENER = build_opener(ProxyHandler({}))
PACK_ROOT = "[...document.querySelectorAll('*')].map(e=>e.shadowRoot).find(r=>r?.querySelector('.pack-list'))"


def read_json(url):
    with OPENER.open(url, timeout=2) as response:
        return json.load(response)


def wait_until(test, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if result := test():
                return result
        except (OSError, ValueError):
            pass
        time.sleep(0.15)
    raise AssertionError("Browser acceptance check timed out")


class CDP:
    def __init__(self, url):
        self.connection = connect(url, proxy=None, max_size=25_000_000)
        self.socket = self.connection.__enter__()
        self.counter = 0

    def call(self, method, **params):
        self.counter += 1
        self.socket.send(json.dumps({"id": self.counter, "method": method, "params": params}))
        while True:
            response = json.loads(self.socket.recv(timeout=20))
            if response.get("id") == self.counter:
                if "error" in response:
                    raise RuntimeError(response["error"])
                return response.get("result", {})

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"])
        return result["result"].get("value")

    def click(self, label, root="document"):
        return self.evaluate(
            f"[...{root}.querySelectorAll('button')].find(b=>b.innerText.trim()==={json.dumps(label)})?.click()"
        )

    def screenshot(self, name):
        result = self.call("Page.captureScreenshot", format="png", captureBeyondViewport=False)
        (ARTIFACTS / name).write_bytes(base64.b64decode(result["data"]))


def seed(config):
    with TestClient(create_app(config)) as client:
        workflow = client.app.state.workflow
        workflow.provider = FixtureProvider()
        runs = []
        for index, prompt in enumerate(
            [
                "A film about the human side of AI",
                "Luxury Automotive Brand Film",
                "Sustainable Futures Campaign",
                "Cinematic Product Launch Film",
            ]
        ):
            record, _ = completed_run(client, count=12 if index == 0 else 3, key=f"ui-review-{index}")
            brief = workflow.brief(record["brief_id"])
            brief.concept = prompt
            workflow.repository.put("brief", brief)
            runs.append(workflow.run(record["id"]))
        run = runs[0]
        for index, item in enumerate(run.results[:3]):
            item.title = "Shared Futures Film" if index < 2 else "A Different Perspective"
            item.annotation.what_it_is = f"Identifiable fixture analysis {index}"
            item.thumbnail = ApprovedThumbnail(
                url=f"https://studio.test/approved/{index}.png",
                source_url=item.source_url,
                usage_status="approved",
                available=True,
            )
        workflow.repository.put("run", run)

        class FixtureFetcher:
            def __init__(self, settings):
                self.deadline = time.monotonic() + 12

            def fetch(self, url, types, limit):
                output = BytesIO()
                image = Image.new("RGB", (640, 360), "#607b78" if "/0.png" in url else "#8c7766")
                ImageDraw.Draw(image).text((28, 310), "Approved test fixture preview", fill="white")
                image.save(output, format="PNG")
                return url, "image/png", output.getvalue()

        client.app.state.thumbnails.fetcher_factory = FixtureFetcher
        client.app.state.thumbnails.for_run(run)
        client.put(
            f"/search-runs/{run.id}/selection",
            json={
                "ordered_ids": [item.id for item in run.results[:3]],
                "expected_version": 0,
            },
        ).raise_for_status()
        return run


def main():
    ARTIFACTS.mkdir(parents=True)
    config = Settings(_env_file=None, data_dir=ARTIFACTS / "database", openai_api_key="test-only-key")
    run = seed(config)
    environment = os.environ.copy()
    environment.update(
        DATA_DIR=str(config.data_dir), OPENAI_API_KEY="test-only-key", BACKEND_URL="http://127.0.0.1:8100"
    )
    processes, logs = [], []

    def start(command, name):
        log = (ARTIFACTS / f"{name}.log").open("w", encoding="utf-8")
        logs.append(log)
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        processes.append(process)

    def api_count():
        return len((ARTIFACTS / "backend.log").read_text(encoding="utf-8").splitlines())

    try:
        start(
            [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8100"],
            "backend",
        )
        wait_until(lambda: read_json("http://127.0.0.1:8100/health"))
        start(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                "frontend/app.py",
                "--server.address",
                "127.0.0.1",
                "--server.port",
                "8601",
                "--server.headless",
                "true",
            ],
            "frontend",
        )
        wait_until(lambda: OPENER.open("http://127.0.0.1:8601/_stcore/health", timeout=1).status == 200)
        profile = ARTIFACTS / "chrome-profile"
        start(
            [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                "--headless=new",
                "--remote-debugging-port=0",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--disable-gpu",
                "--disable-background-networking",
                "about:blank",
            ],
            "chrome",
        )
        port_file = profile / "DevToolsActivePort"
        wait_until(port_file.is_file)
        port = port_file.read_text().splitlines()[0]
        page = wait_until(
            lambda: next(
                (page for page in read_json(f"http://127.0.0.1:{port}/json/list") if page["type"] == "page"),
                None,
            )
        )
        cdp = CDP(page["webSocketDebuggerUrl"])
        cdp.call("Page.enable")
        cdp.call(
            "Emulation.setDeviceMetricsOverride", width=1440, height=1000, deviceScaleFactor=1, mobile=False
        )
        cdp.call("Page.navigate", url=f"http://127.0.0.1:8601/?run={run.id}")
        wait_until(
            lambda: cdp.evaluate(
                "!!document.querySelector('.st-key-pack_panel') && "
                "document.querySelectorAll('[data-testid=stCheckbox]').length===12"
            )
        )
        wait_until(lambda: cdp.evaluate(f"!!({PACK_ROOT})?.querySelector('.pack-row')"))
        time.sleep(0.5)
        geometry = """(() => {
          const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};};
          const row=document.querySelector('.st-key-research_workspace [data-testid=stHorizontalBlock]');
          const cols=[...row.children].filter(e=>e.matches('[data-testid=stColumn]'));
          return {direction:getComputedStyle(row).flexDirection,columns:cols.map(rect),
            previews:[...document.querySelectorAll('.rf-preview')].map(rect),
            pack:rect(document.querySelector('.st-key-pack_panel')),
            overflow:document.documentElement.scrollWidth>innerWidth};
        })()"""
        desktop = cdp.evaluate(geometry)
        assert desktop["direction"] == "row"
        assert abs(desktop["previews"][0]["y"] - desktop["previews"][1]["y"]) < 2, desktop
        assert desktop["previews"][1]["x"] > desktop["previews"][0]["x"]
        assert all(abs(preview["w"] / preview["h"] - 16 / 9) < 0.02 for preview in desktop["previews"])
        cdp.screenshot("desktop.png")
        calls = api_count()
        cdp.click("View Details")
        wait_until(lambda: cdp.evaluate("!!document.querySelector('[role=dialog]')"))
        assert (
            cdp.evaluate("document.querySelector('[role=dialog]').querySelectorAll('textarea').length") == 0
        )
        assert cdp.evaluate(
            "document.querySelector('[role=dialog]').innerText.includes('Production Considerations')"
        )
        cdp.screenshot("details.png")
        cdp.click("Remove from Pack", "document.querySelector('[role=dialog]')")
        wait_until(
            lambda: cdp.evaluate("document.querySelector('[role=dialog]')?.innerText.includes('Add to Pack')")
        )
        cdp.click("Add to Pack", "document.querySelector('[role=dialog]')")
        wait_until(
            lambda: cdp.evaluate(
                "document.querySelector('[role=dialog]')?.innerText.includes('Remove from Pack')"
            )
        )
        cdp.click("Done", "document.querySelector('[role=dialog]')")
        wait_until(lambda: cdp.evaluate("!document.querySelector('[role=dialog]')"))
        assert api_count() == calls, "Details must not request additional API data"
        print("Read-only details and selection: zero additional API requests", flush=True)

        # Use trusted pointer events rather than injecting the Python selection state.
        coordinates = cdp.evaluate(f"""(() => {{const r=({PACK_ROOT});
          const rows=[...r.querySelectorAll('.pack-row')];
          const a=rows[0].querySelector('.drag-handle').getBoundingClientRect();
          const b=rows[1].getBoundingClientRect();
          return {{x:a.x+a.width/2,y:a.y+a.height/2,target:b.bottom+5}};}})()""")
        cdp.call("Input.dispatchMouseEvent", type="mouseMoved", x=coordinates["x"], y=coordinates["y"])
        cdp.call(
            "Input.dispatchMouseEvent",
            type="mousePressed",
            x=coordinates["x"],
            y=coordinates["y"],
            button="left",
            clickCount=1,
        )
        cdp.call(
            "Input.dispatchMouseEvent",
            type="mouseMoved",
            x=coordinates["x"],
            y=coordinates["target"],
            button="left",
            buttons=1,
        )
        cdp.call(
            "Input.dispatchMouseEvent",
            type="mouseReleased",
            x=coordinates["x"],
            y=coordinates["target"],
            button="left",
            clickCount=1,
        )
        time.sleep(0.8)
        displayed = cdp.evaluate(f"[...({PACK_ROOT}).querySelectorAll('.pack-row')].map(e=>e.dataset.id)")
        assert displayed == [run.results[2].id, run.results[1].id, run.results[0].id], displayed
        cdp.screenshot("pack-reordered.png")
        cdp.evaluate(f"({PACK_ROOT}).querySelector('.remove').click()")
        wait_until(lambda: cdp.evaluate(f"({PACK_ROOT})?.querySelectorAll('.pack-row').length===2"))
        time.sleep(0.5)
        displayed = cdp.evaluate(f"[...({PACK_ROOT}).querySelectorAll('.pack-row')].map(e=>e.dataset.id)")
        cdp.click("Export Reference Pack")
        wait_until(
            lambda: cdp.evaluate("document.body.innerText.includes('Download Reference Pack (.docx)')")
        )
        saved = read_json(f"http://127.0.0.1:8100/search-runs/{run.id}")["selection"]
        assert saved["ordered_ids"] == displayed, (saved, displayed)
        with Repository(config.data_dir / "reference_finder.sqlite3").connection() as connection:
            export = json.loads(
                connection.execute(
                    "SELECT payload FROM records WHERE kind='export' ORDER BY rowid DESC LIMIT 1"
                ).fetchone()["payload"]
            )
        document = Document(config.data_dir / "exports" / export["filename"])
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        assert text.index("Identifiable fixture analysis 1") < text.index("Identifiable fixture analysis 0")
        print("Pointer drag, duplicate titles, row removal and exact export order passed", flush=True)
        samples = {}
        for width in [1200, 1024, 768, 390]:
            cdp.call(
                "Emulation.setDeviceMetricsOverride",
                width=width,
                height=1000,
                deviceScaleFactor=1,
                mobile=False,
            )
            time.sleep(0.4)
            sample = cdp.evaluate(geometry)
            samples[str(width)] = sample
            assert sample["direction"] == ("row" if width > 1100 else "column"), sample
            assert not sample["overflow"], sample
            cdp.evaluate("document.querySelector('[data-testid=stMain]').scrollTop=0")
            cdp.screenshot(f"width-{width}.png")
        cdp.call(
            "Emulation.setDeviceMetricsOverride", width=1440, height=1000, deviceScaleFactor=1, mobile=False
        )
        time.sleep(0.4)
        cdp.click("Luxury Automotive Brand Film")
        wait_until(
            lambda: cdp.evaluate(
                "document.body.innerText.includes('Luxury Automotive Brand Film') && "
                "document.querySelectorAll('[data-testid=stCheckbox]').length===3"
            )
        )
        cdp.click("Human Side of AI")
        wait_until(lambda: cdp.evaluate("document.querySelectorAll('[data-testid=stCheckbox]').length===12"))
        wait_until(
            lambda: (
                cdp.evaluate(
                    f"JSON.stringify([...({PACK_ROOT})?.querySelectorAll('.pack-row') ?? []]"
                    ".map(e=>e.dataset.id))"
                )
                == json.dumps(displayed, separators=(",", ":"))
            )
        )
        (ARTIFACTS / "report.json").write_text(
            json.dumps(
                {
                    "desktop": desktop,
                    "responsive": samples,
                    "export_order": displayed,
                    "details_api_calls": 0,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Responsive layout and clickable history passed. Artifacts: {ARTIFACTS}", flush=True)
        cdp.connection.__exit__(None, None, None)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
