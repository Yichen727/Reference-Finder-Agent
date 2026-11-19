"""Chrome acceptance check with isolated test records; no OpenAI calls or user-data writes."""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from io import BytesIO
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from websockets.sync.client import connect  # noqa: E402

from backend.core.config import Settings  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.repositories.sqlite import Repository  # noqa: E402
from backend.schemas.models import ApprovedThumbnail  # noqa: E402
from tests.conftest import completed_run  # noqa: E402
from tests.fixtures import FixtureProvider  # noqa: E402
from tests.pptx_helpers import slide_text, slides_from_bytes  # noqa: E402

ARTIFACTS = Path(tempfile.gettempdir()) / "atomized-ui-review" / str(time.time_ns())
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
            f"[...{root}.querySelectorAll('button')].find(b=>b.innerText.trim()==={json.dumps(label)}"
            " && b.getBoundingClientRect().width>0 && !b.disabled)?.click()"
        )

    def screenshot(self, name):
        result = self.call("Page.captureScreenshot", format="png", captureBeyondViewport=False)
        (ARTIFACTS / name).write_bytes(base64.b64decode(result["data"]))

    def choose_multiselect(self, key, value):
        field = f"document.querySelector('.st-key-{key} input')"
        wait_until(lambda: self.evaluate(f"!!({field})"))
        self.evaluate(field + ".focus()")
        self.call("Input.insertText", text=value)
        option = (
            "[...document.querySelectorAll('[role=option]')]"
            f".find(e=>e.innerText.trim()==={json.dumps(value)})"
        )
        wait_until(lambda: self.evaluate(f"!!({option})"))
        self.evaluate(option + ".click()")

    def pack_state(self):
        return self.evaluate(f"""(() => {{
          const list=({PACK_ROOT})?.querySelector('.pack-list');
          return list ? {{ids:[...list.children].map(row=>row.dataset.id),
            revision:Number(list.dataset.revision)}} : null;
        }})()""")

    def drag_pack(self, source, target, *, selector=".pack-title", cancel=False, touch=False, outside=False):
        before = self.pack_state()
        expected = list(before["ids"])
        item = expected.pop(source)
        expected.insert(target, item)
        coordinates = self.evaluate(f"""(() => {{
          const rows=[...({PACK_ROOT}).querySelectorAll('.pack-row')];
          const a=rows[{source}].querySelector({json.dumps(selector)}).getBoundingClientRect();
          const b=rows[{target}].getBoundingClientRect();
          return {{x:a.x+a.width/2,y:a.y+a.height/2,
            target:{source} < {target} ? b.bottom-2 : b.top+2}};
        }})()""")
        x, y = coordinates["x"], coordinates["y"]
        if touch:
            self.call("Emulation.setTouchEmulationEnabled", enabled=True)
            self.call("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": x, "y": y}])
        else:
            self.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
            self.call("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
        for step in range(1, 19):
            position = y + (coordinates["target"] - y) * step / 18
            if touch:
                self.call("Input.dispatchTouchEvent", type="touchMove", touchPoints=[{"x": x, "y": position}])
            else:
                self.call(
                    "Input.dispatchMouseEvent", type="mouseMoved", x=x, y=position, button="left", buttons=1
                )
            time.sleep(0.03)
        if cancel:
            self.call(
                "Input.dispatchKeyEvent",
                type="keyDown",
                key="Escape",
                code="Escape",
                windowsVirtualKeyCode=27,
            )
            self.call(
                "Input.dispatchKeyEvent", type="keyUp", key="Escape", code="Escape", windowsVirtualKeyCode=27
            )
        if touch:
            self.call("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
            self.call("Emulation.setTouchEmulationEnabled", enabled=False)
        else:
            if outside:
                x = 10
                self.call(
                    "Input.dispatchMouseEvent", type="mouseMoved", x=x, y=position, button="left", buttons=1
                )
            self.call(
                "Input.dispatchMouseEvent", type="mouseReleased", x=x, y=position, button="left", clickCount=1
            )
        if cancel:
            time.sleep(0.25)
            assert self.pack_state() == before, (before, self.pack_state())
        else:
            wait_until(lambda: self.pack_state() == {"ids": expected, "revision": before["revision"] + 1})


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
            record, _ = completed_run(client, count=4 if index == 0 else 3, key=f"ui-review-{index}")
            brief = workflow.brief(record["brief_id"])
            brief.concept = prompt
            workflow.repository.put("brief", brief)
            runs.append(workflow.run(record["id"]))
        run = runs[0]
        for item in run.results:
            item.platform = "Vimeo"
            item.source_category = "Video & Film"
        run.results[0].platform = "YouTube"
        run.results[0].strand = "Craft"
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
    config = Settings(
        _env_file=None,
        data_dir=ARTIFACTS / "database",
        openai_api_key="test-only-key",
    )
    run = seed(config)
    environment = os.environ.copy()
    environment.update(
        DATA_DIR=str(config.data_dir),
        OPENAI_API_KEY="test-only-key",
        BACKEND_URL="http://127.0.0.1:8100",
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
        repository = Repository(config.data_dir / "reference_finder.sqlite3")
        running = type(run).model_validate(repository.get("run", run.id))
        running.status, running.stage = "running", "starting_external_search"
        repository.put("run", running)
        assert read_json("http://127.0.0.1:8100/health")["max_references"] == 20
        cdp.call("Page.navigate", url=f"http://127.0.0.1:8601/?run={run.id}")
        wait_until(lambda: cdp.evaluate("!!document.body"))
        for stage, label, value in [
            ("starting_external_search", "Selecting Sources", 20),
            ("searching_1_of_4", "Searching References", 40),
            ("checking_public_pages", "Verifying & Ranking", 60),
        ]:
            running.stage = stage
            repository.put("run", running)
            wait_until(
                lambda: cdp.evaluate(
                    f"document.body.innerText.includes({json.dumps(label)}) && "
                    f"document.querySelector('[role=progressbar]')?.getAttribute('aria-valuenow')==='{value}'"
                )
            )
            assert not cdp.evaluate("!!document.querySelector('.st-key-pack_panel')")
            assert not cdp.evaluate(
                "[...document.querySelectorAll('button')].some(e=>e.innerText==='Find References')"
            )
        cdp.screenshot("search-progress.png")
        cdp.call(
            "Emulation.setDeviceMetricsOverride", width=390, height=1000, deviceScaleFactor=1, mobile=False
        )
        time.sleep(0.3)
        assert cdp.evaluate("document.documentElement.scrollWidth<=innerWidth")
        cdp.screenshot("search-progress-narrow.png")
        cdp.call(
            "Emulation.setDeviceMetricsOverride", width=1440, height=1000, deviceScaleFactor=1, mobile=False
        )
        running.status = running.stage = "completed"
        repository.put("run", running)
        wait_until(
            lambda: cdp.evaluate(
                "!!document.querySelector('.st-key-pack_panel') && "
                "document.querySelectorAll('[class*=st-key-card_select_]').length===4"
            )
        )
        assert not cdp.evaluate("!!document.querySelector('[role=progressbar]')")
        print("Actual stage progress, narrow layout and transition to 4 result cards passed", flush=True)
        wait_until(lambda: cdp.evaluate(f"!!({PACK_ROOT})?.querySelector('.pack-row')"))
        time.sleep(0.5)
        compact_header = """(() => {
          const summary=document.querySelector('.st-key-results_summary');
          const card=document.querySelector('[class*=st-key-reference_card_]');
          return summary.innerText.includes('Results · Human Side of AI') &&
            !summary.innerText.includes('A film about the human side of AI') &&
            card.getBoundingClientRect().top-summary.getBoundingClientRect().bottom<=12;
        })()"""
        cdp.screenshot("compact-header.png")
        assert cdp.evaluate(compact_header), cdp.evaluate("""(() => {
          const summary=document.querySelector('.st-key-results_summary');
          const card=document.querySelector('[class*=st-key-reference_card_]');
          return {text:summary.innerText, summary:summary.getBoundingClientRect().toJSON(),
            card:card.getBoundingClientRect().toJSON()};
        })()""")
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
        assert cdp.evaluate(f"""(() => {{
          const card=document.querySelector('.st-key-reference_card_{run.results[0].id}');
          return card.innerText.includes('Identifiable fixture analysis 0') &&
            !card.innerText.includes('What It Is') && !card.innerText.includes('What to Borrow') &&
            !card.innerText.includes('A specific creative principle.');
        }})()"""), "Cards must show the existing work description without an annotation label"
        scroll_regions = """[...document.querySelectorAll('.st-key-reference_results *')]
          .filter(e=>e.clientWidth &&
            ((['auto','scroll'].includes(getComputedStyle(e).overflowX) && e.scrollWidth>e.clientWidth) ||
             (['auto','scroll'].includes(getComputedStyle(e).overflowY) && e.scrollHeight>e.clientHeight)))
          .map(e=>({key:e.className, text:e.innerText.slice(0,60),
            overflowX:getComputedStyle(e).overflowX, overflowY:getComputedStyle(e).overflowY,
            width:e.clientWidth, scrollWidth:e.scrollWidth,
            height:e.clientHeight, scrollHeight:e.scrollHeight}))"""
        assert not cdp.evaluate(scroll_regions), cdp.evaluate(scroll_regions)
        assert desktop["direction"] == "row"
        assert abs(desktop["previews"][0]["y"] - desktop["previews"][1]["y"]) < 2, desktop
        assert desktop["previews"][1]["x"] > desktop["previews"][0]["x"]
        assert all(abs(preview["w"] / preview["h"] - 16 / 9) < 0.02 for preview in desktop["previews"])
        assert cdp.evaluate(
            "document.querySelectorAll('h1').length===1 && "
            "document.querySelector('h1').innerText==='Atomized Reference Finder'"
        )
        action_geometry = f"""[...document.querySelector('.st-key-card_actions_{run.results[0].id}')
          .querySelectorAll('button,a')].map(e=>e.getBoundingClientRect())
          .filter(r=>r.width>0).map(r=>({{x:r.x,y:r.y,w:r.width,h:r.height}}))"""
        actions = cdp.evaluate(action_geometry)
        assert len(actions) == 3, actions
        assert max(action["y"] for action in actions) - min(action["y"] for action in actions) < 2, actions
        assert abs(actions[2]["w"] - actions[2]["h"]) < 1 and actions[2]["w"] == 40, actions
        assert actions[0]["w"] > actions[1]["w"] > actions[2]["w"], actions
        footer_width = cdp.evaluate(
            f"document.querySelector('.st-key-card_actions_{run.results[0].id}').getBoundingClientRect().width"
        )
        assert actions[0]["w"] > footer_width * 0.52, actions
        label_alignment = f"""(() => {{
          const row=document.querySelector('.st-key-card_actions_{run.results[0].id}');
          const labels=[...row.querySelectorAll('button,a')].filter(e=>e.getBoundingClientRect().width>0)
            .slice(0,2).map(e=>e.querySelector('p'));
          const ranges=labels.map(label=>{{const r=document.createRange();r.selectNodeContents(label);
            const b=r.getBoundingClientRect();return {{top:b.top,height:b.height}};}});
          return Math.abs(ranges[0].top-ranges[1].top)<1 && Math.abs(ranges[0].height-ranges[1].height)<1;
        }})()"""
        assert cdp.evaluate(label_alignment), "Details and Link text baselines differ"
        icon_alignment = """[...document.querySelectorAll('[class*=st-key-card_select_] button')]
        .every(button=>{
          const icon=button.querySelector('[data-testid=stIconMaterial]');
          if (!icon) return false;
          const b=button.getBoundingClientRect(), i=icon.getBoundingClientRect();
          return Math.abs((b.x+b.width/2)-(i.x+i.width/2))<.5
            && Math.abs((b.y+b.height/2)-(i.y+i.height/2))<.5;
        })"""
        assert cdp.evaluate(icon_alignment), "Add/check icon is not centred"
        footer = cdp.evaluate(
            f"document.querySelector('.st-key-card_actions_{run.results[0].id}').getBoundingClientRect().right"
        )
        assert abs(actions[2]["x"] + actions[2]["w"] - footer) < 2, actions
        assert max(action["h"] for action in actions) - min(action["h"] for action in actions) < 2, actions
        cdp.screenshot("desktop.png")
        filter_button = (
            f"([...document.querySelectorAll('.st-key-result_filters_{run.id} button')]"
            ".find(button=>button.getBoundingClientRect().width>0))"
        )
        assert cdp.evaluate(f"""(() => {{
          const heading=document.querySelector('.st-key-results_heading_{run.id}');
          const title=heading.querySelector('h3').getBoundingClientRect();
          const icon=({filter_button}).getBoundingClientRect();
          return icon.x>title.x && icon.right<=heading.getBoundingClientRect().right+1;
        }})()""")
        before_filters = cdp.pack_state()
        calls_before_filters = api_count()
        cdp.evaluate(filter_button + ".click()")
        cdp.choose_multiselect(f"source_filter_{run.id}", "Vimeo")
        wait_until(
            lambda: cdp.evaluate("document.querySelectorAll('[class*=st-key-card_select_]').length===3")
        )
        wait_until(lambda: cdp.pack_state() == before_filters)
        assert cdp.evaluate(filter_button + ".innerText.includes('1')")
        assert cdp.evaluate(
            "document.querySelector('.st-key-results_summary').innerText"
            ".includes('3 Results · Human Side of AI')"
        )
        cdp.choose_multiselect(f"strand_filter_{run.id}", "Craft")
        wait_until(
            lambda: cdp.evaluate("document.querySelectorAll('[class*=st-key-card_select_]').length===0")
        )
        wait_until(lambda: cdp.pack_state() == before_filters)
        assert cdp.evaluate(filter_button + ".innerText.includes('2')")
        cdp.screenshot("filter-popover.png")
        cdp.click("Clear Filters")
        wait_until(
            lambda: cdp.evaluate("document.querySelectorAll('[class*=st-key-card_select_]').length===4")
        )
        wait_until(lambda: cdp.pack_state() == before_filters)
        assert api_count() == calls_before_filters
        assert not cdp.evaluate(filter_button + ".innerText.includes('2')")
        cdp.evaluate(filter_button + ".click()")
        print("Filter icon, active count, immediate updates and Clear Filters preserve the pack", flush=True)
        # The icon toggles selection through the existing callback, without duplicates.
        icon = f"document.querySelector('.st-key-card_select_{run.results[3].id} button')"
        cdp.evaluate(icon + ".click()")
        wait_until(lambda: cdp.pack_state()["ids"] == [item.id for item in run.results[:4]])
        assert cdp.evaluate(icon + ".innerText.includes('check')")
        assert cdp.evaluate(icon_alignment), "Selected checkmark is not centred"
        cdp.evaluate(icon + ".click()")
        wait_until(lambda: cdp.pack_state()["ids"] == [item.id for item in run.results[:3]])
        calls = api_count()
        cdp.click("View Details")
        wait_until(lambda: cdp.evaluate("!!document.querySelector('[role=dialog]')"))
        assert (
            cdp.evaluate("document.querySelector('[role=dialog]').querySelectorAll('textarea').length") == 0
        )
        assert cdp.evaluate(
            "(()=>{const text=document.querySelector('[role=dialog]').innerText;"
            "return ['What It Is','What to Borrow','Why It Fits Your Brief','Source & Rights']"
            ".every(label=>text.includes(label)) && !text.includes('Production Considerations')"
            " && !text.includes('Recommendation details and assumptions');})()"
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

        # Continuous human-like movement must receive pointerup after rows move in the DOM.
        cdp.drag_pack(0, 1, selector=".drag-handle")
        cdp.drag_pack(2, 0)
        cdp.drag_pack(0, 2, outside=True)
        cdp.drag_pack(0, 1, cancel=True)
        cdp.drag_pack(0, 1, touch=True)
        cdp.drag_pack(1, 0, touch=True)
        before_keyboard = cdp.pack_state()
        cdp.evaluate(f"({PACK_ROOT}).querySelector('.drag-handle').focus()")
        cdp.call(
            "Input.dispatchKeyEvent",
            type="keyDown",
            key="ArrowDown",
            code="ArrowDown",
            windowsVirtualKeyCode=40,
        )
        cdp.call(
            "Input.dispatchKeyEvent",
            type="keyUp",
            key="ArrowDown",
            code="ArrowDown",
            windowsVirtualKeyCode=40,
        )
        wait_until(lambda: cdp.pack_state()["revision"] == before_keyboard["revision"] + 1)
        cdp.drag_pack(1, 0)
        displayed = cdp.evaluate(f"[...({PACK_ROOT}).querySelectorAll('.pack-row')].map(e=>e.dataset.id)")
        assert displayed == [run.results[2].id, run.results[1].id, run.results[0].id], displayed
        cdp.screenshot("pack-reordered.png")
        cdp.evaluate(f"({PACK_ROOT}).querySelector('.remove').click()")
        wait_until(lambda: cdp.evaluate(f"({PACK_ROOT})?.querySelectorAll('.pack-row').length===2"))
        time.sleep(0.5)
        displayed = cdp.evaluate(f"[...({PACK_ROOT}).querySelectorAll('.pack-row')].map(e=>e.dataset.id)")
        cdp.click("Export Reference Pack")
        wait_until(
            lambda: cdp.evaluate("document.body.innerText.includes('Download Reference Pack (.pptx)')")
        )
        saved = read_json(f"http://127.0.0.1:8100/search-runs/{run.id}")["selection"]
        assert saved["ordered_ids"] == displayed, (saved, displayed)
        with Repository(config.data_dir / "reference_finder.sqlite3").connection() as connection:
            export = json.loads(
                connection.execute(
                    "SELECT payload FROM records WHERE kind='export' ORDER BY rowid DESC LIMIT 1"
                ).fetchone()["payload"]
            )
        slides = slides_from_bytes((config.data_dir / "exports" / export["filename"]).read_bytes())
        assert len(slides) == len(displayed) + 3
        text = "\n".join(slide_text(slide) for slide in slides[2:-1])
        assert text.index("Identifiable fixture analysis 1") < text.index("Identifiable fixture analysis 0")
        print(
            "Continuous handle/title dragging, outside release, cancellation, touch and keyboard passed",
            flush=True,
        )
        print("Duplicate titles, row removal and exact PPTX slide order passed", flush=True)
        samples = {}
        for width in [1200, 1101, 1024, 768, 390]:
            cdp.call(
                "Emulation.setDeviceMetricsOverride",
                width=width,
                height=1000,
                deviceScaleFactor=1,
                mobile=False,
            )
            time.sleep(0.4)
            sample = cdp.evaluate(geometry)
            assert not cdp.evaluate(scroll_regions), (width, cdp.evaluate(scroll_regions))
            samples[str(width)] = sample
            assert sample["direction"] == ("row" if width > 1100 else "column"), sample
            assert not sample["overflow"], sample
            actions = cdp.evaluate(action_geometry)
            assert max(action["y"] for action in actions) - min(action["y"] for action in actions) < 2, (
                actions
            )
            assert abs(actions[2]["w"] - actions[2]["h"]) < 1, actions
            footer_width = cdp.evaluate(
                f"document.querySelector('.st-key-card_actions_{run.results[0].id}').getBoundingClientRect().width"
            )
            assert actions[0]["w"] > footer_width * 0.52, (width, actions)
            assert cdp.evaluate(label_alignment), f"Text baselines differ at {width}px"
            assert cdp.evaluate(icon_alignment), f"Add/check icons are off-centre at {width}px"
            assert cdp.evaluate(compact_header), f"Results header is not compact at {width}px"
            cdp.evaluate("document.querySelector('[data-testid=stMain]').scrollTop=0")
            cdp.screenshot(f"width-{width}.png")
            assert cdp.evaluate(f"""(() => {{
              const row=document.querySelector('.st-key-results_heading_{run.id}').getBoundingClientRect();
              const icon=({filter_button}).getBoundingClientRect();
              return icon.right<=row.right+1 && icon.width<100 &&
                Math.abs(icon.top+icon.height/2-row.top-row.height/2)<2;
            }})()"""), cdp.evaluate(f"""(() => {{
              const row=document.querySelector('.st-key-results_heading_{run.id}');
              return {{width:{width}, row:row.getBoundingClientRect().toJSON(),
                button:({filter_button}).getBoundingClientRect().toJSON()}};
            }})()""")
            assert cdp.evaluate(f"""(() => {{
              const label=document.querySelector('.st-key-card_actions_{run.results[0].id} a p');
              return !!label && label.scrollWidth <= label.clientWidth + 1;
            }})()"""), f"Link label is clipped at {width}px"
        cdp.call(
            "Emulation.setDeviceMetricsOverride", width=1440, height=1000, deviceScaleFactor=1, mobile=False
        )
        time.sleep(0.4)
        cdp.click("Luxury Automotive Brand Film")
        wait_until(
            lambda: cdp.evaluate(
                "document.body.innerText.includes('Luxury Automotive Brand Film') && "
                "document.querySelectorAll('[class*=st-key-card_select_]').length===3"
            )
        )
        cdp.click("Human Side of AI")
        wait_until(
            lambda: cdp.evaluate("document.querySelectorAll('[class*=st-key-card_select_]').length===4")
        )
        wait_until(
            lambda: (
                cdp.evaluate(
                    f"JSON.stringify([...({PACK_ROOT})?.querySelectorAll('.pack-row') ?? []]"
                    ".map(e=>e.dataset.id))"
                )
                == json.dumps(displayed, separators=(",", ":"))
            )
        )
        # Rename is immediate and persistent; deleting requires an explicit confirmation.
        cdp.evaluate(f"document.querySelector('.st-key-manage_{run.id} button').click()")
        cdp.click("Rename")
        wait_until(lambda: cdp.evaluate("document.body.innerText.includes('Rename Research')"))
        wait_until(lambda: cdp.evaluate("!!document.querySelector('[role=dialog] input')"))
        cdp.evaluate("document.querySelector('[role=dialog] input').focus()")
        cdp.call(
            "Input.dispatchKeyEvent",
            type="keyDown",
            key="a",
            code="KeyA",
            modifiers=2,
            windowsVirtualKeyCode=65,
        )
        cdp.call(
            "Input.dispatchKeyEvent",
            type="keyUp",
            key="a",
            code="KeyA",
            modifiers=2,
            windowsVirtualKeyCode=65,
        )
        cdp.call("Input.insertText", text="Human AI References")
        cdp.evaluate("document.activeElement.blur()")
        time.sleep(0.5)
        cdp.click("Save Title")
        wait_until(
            lambda: cdp.evaluate(
                "!document.querySelector('[role=dialog]') && "
                "document.body.innerText.includes('Human AI References')"
            )
        )
        assert cdp.pack_state()["ids"] == displayed
        assert cdp.evaluate(
            "document.querySelector('.st-key-results_summary').innerText"
            ".includes('Results · Human AI References')"
        )
        assert (
            next(item for item in read_json("http://127.0.0.1:8100/search-runs") if item["id"] == run.id)[
                "title"
            ]
            == "Human AI References"
        )
        cdp.screenshot("renamed.png")
        cdp.evaluate(f"document.querySelector('.st-key-manage_{run.id} button').click()")
        cdp.click("Delete")
        wait_until(lambda: cdp.evaluate("document.body.innerText.includes('Delete Research?')"))
        assert read_json(f"http://127.0.0.1:8100/search-runs/{run.id}")["id"] == run.id
        cdp.click("Cancel")
        wait_until(lambda: cdp.evaluate("!document.querySelector('[role=dialog]')"))
        assert cdp.pack_state()["ids"] == displayed
        cdp.evaluate(f"document.querySelector('.st-key-manage_{run.id} button').click()")
        cdp.click("Delete")
        wait_until(lambda: cdp.evaluate("document.body.innerText.includes('Delete Research?')"))
        cdp.click("Delete Research")
        wait_until(
            lambda: cdp.evaluate(
                "document.body.innerText.includes('Find References') && "
                "!document.querySelector('.st-key-pack_panel')"
            )
        )
        assert run.id not in [item["id"] for item in read_json("http://127.0.0.1:8100/search-runs")]
        assert cdp.evaluate("document.querySelector('h1').innerText==='Atomized Reference Finder'")
        cdp.screenshot("deleted-home.png")
        # The new form uses backend metadata, independent of the saved run's platform labels.
        expected_sources = [item["name"] for item in read_json("http://127.0.0.1:8100/source-categories")]
        source_tags = (
            "document.querySelectorAll('.st-key-brief_sources [aria-label=\"Selected values\"] [data-tag]')"
        )
        wait_until(lambda: cdp.evaluate(f"{source_tags}.length===8"))
        assert cdp.evaluate(
            "![...document.querySelectorAll('summary')].some(e=>e.innerText.includes('Advanced Options'))"
            " && document.querySelector('.st-key-brief_sources').getBoundingClientRect().height>0"
        )
        assert len(expected_sources) == 8
        assert cdp.evaluate(
            f"{json.dumps(expected_sources)}.every(name=>document.querySelector('.st-key-brief_sources').innerText.includes(name))"
        )
        cdp.screenshot("eight-source-options-desktop.png")
        cdp.call(
            "Emulation.setDeviceMetricsOverride", width=390, height=1000, deviceScaleFactor=1, mobile=False
        )
        time.sleep(0.3)
        assert cdp.evaluate("document.documentElement.scrollWidth<=innerWidth")
        cdp.screenshot("eight-source-options-narrow.png")
        cdp.call(
            "Emulation.setDeviceMetricsOverride", width=1440, height=1000, deviceScaleFactor=1, mobile=False
        )
        cdp.evaluate(
            "document.querySelector('.st-key-brief_sources "
            'button[aria-label="Remove Podcasts & Interviews"]\').click()'
        )
        wait_until(lambda: cdp.evaluate(f"{source_tags}.length===7"))
        cdp.click("Find References")  # Empty prompt: validation rerun, never starts research.
        wait_until(lambda: cdp.evaluate("document.body.innerText.includes('Describe your creative idea.')"))
        assert cdp.evaluate(f"{source_tags}.length===7")
        assert cdp.evaluate(
            f"[...{source_tags}].every(tag=>tag.getAttribute('aria-label')!=='Podcasts & Interviews')"
        )
        print(
            "Eight source defaults, optional podcast deselection, validation rerun and narrow form passed",
            flush=True,
        )
        print(
            "Header, compact actions, square icon toggle, rename and confirmed deletion passed",
            flush=True,
        )
        (ARTIFACTS / "report.json").write_text(
            json.dumps(
                {
                    "desktop": desktop,
                    "responsive": samples,
                    "export_order": displayed,
                    "details_api_calls": 0,
                    "source_categories": expected_sources,
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
                if os.name == "nt":
                    # The venv launcher may own a base-Python child; stop this test's whole tree.
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                else:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in logs:
            log.close()
        profile = ARTIFACTS / "chrome-profile"
        if profile.exists():
            temporary_root = Path(tempfile.gettempdir()).resolve()
            if (
                not ARTIFACTS.resolve().is_relative_to(temporary_root)
                or profile.resolve().parent != ARTIFACTS.resolve()
            ):
                raise RuntimeError("Unexpected browser profile path; cleanup stopped")
            shutil.rmtree(profile)


if __name__ == "__main__":
    main()
