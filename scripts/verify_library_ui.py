"""Chrome acceptance for global favorites/packs with isolated data and no OpenAI calls."""

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
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from backend.core.config import Settings  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.repositories.sqlite import Repository  # noqa: E402
from backend.services.thumbnails import PREVIEW_VERSION  # noqa: E402
from scripts import verify_ui as browser  # noqa: E402
from tests.fixtures import FixtureProvider  # noqa: E402
from tests.pptx_helpers import slide_text, slides_from_bytes  # noqa: E402
from tests.test_library import add, current, locator, research  # noqa: E402

ARTIFACTS = Path(tempfile.gettempdir()) / "atomized-library-review" / str(time.time_ns())
API, FRONTEND = "http://127.0.0.1:8110", "http://127.0.0.1:8611"


def request(path, method="GET", payload=None, *, binary=False):
    body = json.dumps(payload).encode() if payload is not None else None
    req = Request(API + path, data=body, method=method, headers={"Content-Type": "application/json"})
    with browser.OPENER.open(req, timeout=15) as response:
        content = response.read()
    return content if binary else json.loads(content)


def seed(config):
    with TestClient(create_app(config)) as client:
        client.app.state.workflow.provider = FixtureProvider()
        first, second = research(client), research(client, "second", distinct=True)
        first["title"] = "Human Side of AI - Creative Teams, Everyday Work and Shared Possibilities"
        client.patch(f"/search-runs/{first['id']}/title", json={"title": first["title"]}).raise_for_status()
        thumbnails = client.app.state.thumbnails
        for run in (first, second):
            stored = client.app.state.workflow.run(run["id"])
            stored.results[0].annotation.what_to_borrow = (
                "- Frame the action around one person.\n- Repeat a clear visual motif.\n"
                "- End with a shared purpose."
            )
            client.app.state.workflow.repository.put("run", stored)
            for index, item in enumerate(stored.results):
                image = BytesIO()
                Image.new("RGB", (640, 360), "#607b78" if run is first else "#8c7766").save(image, "PNG")
                preview = {
                    "image": "data:image/png;base64," + base64.b64encode(image.getvalue()).decode(),
                    "source_url": "https://studio.test/fixture.png",
                    "basis": "Offline test fixture", "preview_version": PREVIEW_VERSION,
                }
                images = [preview.copy()]
                if index == 0:
                    for color in ("#5e7198", "#ae724a"):
                        image = BytesIO()
                        Image.new("RGB", (640, 360), color).save(image, "PNG")
                        images.append({**preview, "image": "data:image/png;base64,"
                                       + base64.b64encode(image.getvalue()).decode()})
                thumbnails.store.save_preview(
                    thumbnails.cache_key(item),
                    {**preview, "images": images},
                    3600,
                )
        favorites = [client.put("/favorites", json=locator(first, index)).json() for index in (0, 1)]
        pack = current(client)
        for run, index in ((first, 0), (second, 1), (first, 2)):
            pack = add(client, pack, locator(run, index))
        return first, second, favorites, pack


def main():
    ARTIFACTS.mkdir(parents=True)
    browser.ARTIFACTS = ARTIFACTS
    # Streamlit preloads secret files at startup, before the application runs.
    # Isolate browser tests from the developer's actual security code and keys.
    test_secrets = ARTIFACTS / "secrets.toml"
    test_secrets.write_text('APP_ACCESS_CODE = "offline-browser-code"\n', encoding="utf-8")
    config = Settings(_env_file=None, data_dir=ARTIFACTS / "database", openai_api_key="test-only-key")
    first, second, favorites, original = seed(config)
    environment = {
        **os.environ,
        "DATA_DIR": str(config.data_dir),
        "OPENAI_API_KEY": "test-only-key",
        "PREVIEW_APPROVED_DOMAINS": "[]",
        "BACKEND_URL": API,
        "APP_ACCESS_CODE": "offline-browser-code",
    }
    processes, logs = [], []
    cdp = None

    def start(command, name):
        log = (ARTIFACTS / f"{name}.log").open("w", encoding="utf-8")
        logs.append(log)
        processes.append(
            subprocess.Popen(
                command,
                cwd=ROOT,
                env=environment,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        )
        (ARTIFACTS / "processes.json").write_text(
            json.dumps([{"pid": process.pid, "command": process.args} for process in processes]),
            encoding="utf-8",
        )

    def click_key(key):
        selector = json.dumps(f".st-key-{key} button")
        browser.wait_until(
            lambda: cdp.evaluate(f"document.querySelector({selector})?.getBoundingClientRect().width>0")
        )
        cdp.evaluate(f"document.querySelector({selector}).click()")

    def cards(count):
        return cdp.evaluate(f"document.querySelectorAll('[class*=st-key-card_select_]').length==={count}")

    def unlock_workspace():
        browser.wait_until(lambda: cdp.evaluate("!!document.querySelector('input[type=password]')"))
        assert not cards(4), "Research must stay hidden before verification"
        cdp.evaluate("document.querySelector('input[type=password]').focus()")
        cdp.call("Input.insertText", text="offline-browser-code")
        cdp.evaluate("document.activeElement.blur()")
        cdp.click("Unlock")

    def pack_count(count):
        value = cdp.pack_state()
        return value is not None and len(value["ids"]) == count

    def check_pack_dialog(action):
        for width in (1440, 390):
            cdp.call(
                "Emulation.setDeviceMetricsOverride", width=width, height=1000,
                deviceScaleFactor=1, mobile=False,
            )
            browser.wait_until(lambda: cdp.evaluate("""(() => {
              const a=document.querySelector('.st-key-submit_pack_management button')
                ?.getBoundingClientRect();
              const b=document.querySelector('.st-key-cancel_pack_management button')
                ?.getBoundingClientRect();
              return a && b && Math.abs(a.top-b.top)<1 && Math.abs(a.width-b.width)<1 && b.left>a.right &&
                document.documentElement.scrollWidth<=innerWidth;
            })()"""))
            cdp.screenshot(f"pack-{action}-dialog-{width}.png")
        cdp.call(
            "Emulation.setDeviceMetricsOverride", width=1440, height=1000, deviceScaleFactor=1, mobile=False,
        )

    def click_at(selector, *, right_edge=False):
        point = cdp.evaluate(f"""(() => {{
          const e=document.querySelector({json.dumps(selector)});
          e.scrollIntoView({{block:'nearest'}});
          const r=e.getBoundingClientRect();
          return {{x:{'r.right-3' if right_edge else 'r.left+r.width/2'},y:r.top+r.height/2}};
        }})()""")
        cdp.call("Input.dispatchMouseEvent", type="mouseMoved", **point)
        for event in ("mousePressed", "mouseReleased"):
            cdp.call("Input.dispatchMouseEvent", type=event, button="left", clickCount=1, **point)

    def check_arrow(key):
        field = f".st-key-{key}"
        arrow = field + ' button[aria-label="Open"]'
        for right_edge in (False, True):
            click_at(arrow, right_edge=right_edge)
            expanded = (
                f"document.querySelector({json.dumps(field + ' input[role=combobox]')})"
                "?.getAttribute('aria-expanded')==='true' && !!document.querySelector('[role=listbox]')"
            )
            browser.wait_until(lambda: cdp.evaluate(expanded), timeout=5)
            time.sleep(0.4)
            assert cdp.evaluate(expanded), f"Arrow dropdown closed after the click: {key}"
            browser.wait_until(lambda: cdp.evaluate("""(() => {
              const list=document.querySelector('[role=listbox]');
              if (!list) return false;
              const r=list.getBoundingClientRect();
              const hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
              return r.width>0 && r.height>0 && list.contains(hit);
            })()"""), timeout=5)
            cdp.screenshot(f"arrow-{key[:20]}.png")
            for event in ("keyDown", "keyUp"):
                cdp.call(
                    "Input.dispatchKeyEvent", type=event, key="Escape", code="Escape",
                    windowsVirtualKeyCode=27,
                )
            browser.wait_until(lambda: cdp.evaluate("!document.querySelector('[role=listbox]')"))

    def check_management_menu(trigger_key, item_keys, screenshot):
        click_key(trigger_key)
        trigger = cdp.evaluate(f"""(() => {{
          const button=document.querySelector('.st-key-'+{json.dumps(trigger_key)}+' button');
          const a=button.getBoundingClientRect(), b=button.querySelector('p').getBoundingClientRect();
          return {{x:Math.abs(a.left+a.width/2-b.left-b.width/2),
            y:Math.abs(a.top+a.height/2-b.top-b.height/2),background:getComputedStyle(button).backgroundColor}};
        }})()""")
        assert trigger["x"] < 1 and trigger["y"] < 1, trigger
        assert trigger["background"] == "rgb(238, 238, 238)", trigger
        selector = '[data-testid="stPopoverBody"]:has([class*="st-key-management_menu_"])'
        browser.wait_until(lambda: cdp.evaluate(f"!!document.querySelector({json.dumps(selector)})"))
        metrics = cdp.evaluate(f"""(() => {{
          const menu=document.querySelector({json.dumps(selector)});
          const keys={json.dumps(item_keys)};
          return {{width:menu.getBoundingClientRect().width,
            dividers:menu.querySelectorAll('hr').length,
            items:keys.map(key=>{{
              const button=menu.querySelector('.st-key-'+key+' button'), style=getComputedStyle(button);
              const icon=button.querySelector('[data-testid=stIconMaterial]');
              const label=button.querySelector('p');
              return {{border:style.borderWidth,background:style.backgroundColor,
                height:button.getBoundingClientRect().height,icon:icon?.innerText,
                icon_size:getComputedStyle(icon).fontSize,
                gap:label.getBoundingClientRect().left-icon.getBoundingClientRect().right,
                label_x:label.getBoundingClientRect().left,
                font:getComputedStyle(label).fontSize}};
            }})}};
        }})()""")
        assert 180 <= metrics["width"] <= 224 and metrics["dividers"] == 1, metrics
        assert all(
            item["border"] == "0px" and item["background"] == "rgba(0, 0, 0, 0)"
            and item["height"] == 36 and item["icon"] in {"add", "edit", "delete"}
            and item["icon_size"] == "18px" and item["font"] == "14px" and item["gap"] >= 8
            for item in metrics["items"]
        ), metrics
        assert max(item["label_x"] for item in metrics["items"]) - min(
            item["label_x"] for item in metrics["items"]
        ) < 1, metrics
        point = cdp.evaluate(f"""(() => {{
          const button=document.querySelector('.st-key-'+{json.dumps(item_keys[0])}+' button');
          const r=button.getBoundingClientRect();
          return {{x:r.left+r.width/2,y:r.top+r.height/2}};
        }})()""")
        cdp.call("Input.dispatchMouseEvent", type="mouseMoved", **point)
        browser.wait_until(lambda: cdp.evaluate(
            f"getComputedStyle(document.querySelector('.st-key-'+{json.dumps(item_keys[0])}+' button'))"
            ".backgroundColor==='rgb(238, 238, 238)'"
        ))
        cdp.screenshot(screenshot)
        for event in ("keyDown", "keyUp"):
            cdp.call(
                "Input.dispatchKeyEvent", type=event, key="Escape", code="Escape", windowsVirtualKeyCode=27
            )
        browser.wait_until(lambda: cdp.evaluate(f"!document.querySelector({json.dumps(selector)})"))

    try:
        start(
            [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8110"],
            "backend",
        )
        browser.wait_until(lambda: request("/health"))
        assert request("/library")["current_pack"]["id"] == original["id"]
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
                "8611",
                "--server.headless",
                "true",
                "--secrets.files",
                str(test_secrets),
            ],
            "frontend",
        )
        browser.wait_until(lambda: browser.OPENER.open(FRONTEND + "/_stcore/health", timeout=1).status == 200)
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
        browser.wait_until(port_file.is_file)
        port = port_file.read_text().splitlines()[0]
        page = browser.wait_until(
            lambda: next(
                (p for p in browser.read_json(f"http://127.0.0.1:{port}/json/list") if p["type"] == "page"),
                None,
            )
        )
        cdp = browser.CDP(page["webSocketDebuggerUrl"])
        cdp.call("Page.enable")
        cdp.call(
            "Emulation.setDeviceMetricsOverride", width=1440, height=1000, deviceScaleFactor=1, mobile=False
        )
        cdp.call("Page.navigate", url=FRONTEND + f"/?run={first['id']}")
        unlock_workspace()
        browser.wait_until(lambda: cards(4) and pack_count(3))
        browser.wait_until(
            lambda: cdp.evaluate("document.querySelectorAll('[class*=st-key-card_bookmark_]').length===4")
        )
        card_gallery = (
            "[...document.querySelectorAll('*')].map(e=>e.shadowRoot)"
            ".find(r=>r?.querySelector('.preview:not(.detail)'))"
        )
        browser.wait_until(lambda: cdp.evaluate(f"!!({card_gallery})?.querySelector('.next')"))
        initial_pack = cdp.pack_state()
        first_image = cdp.evaluate(f"({card_gallery}).querySelector('img').src")
        cdp.evaluate(f"({card_gallery}).querySelector('.next').click()")
        assert cdp.evaluate(f"({card_gallery}).querySelector('.counter').textContent") == "2/3"
        assert cdp.evaluate(f"({card_gallery}).querySelector('img').src") != first_image
        cdp.evaluate(f"({card_gallery}).querySelector('.previous').click()")
        assert cdp.evaluate(f"({card_gallery}).querySelector('.counter').textContent") == "1/3"
        assert cdp.pack_state() == initial_pack
        click_key(f"details_{first['results'][0]['id']}")
        detail_gallery = (
            "[...document.querySelectorAll('*')].map(e=>e.shadowRoot)"
            ".find(r=>r?.querySelector('.preview.detail'))"
        )
        browser.wait_until(lambda: cdp.evaluate(f"!!({detail_gallery})?.querySelector('.next')"))
        cdp.evaluate(f"({detail_gallery}).querySelector('.next').click()")
        assert cdp.evaluate(f"({detail_gallery}).querySelector('.counter').textContent") == "2/3"
        assert cdp.evaluate("document.querySelectorAll('[role=dialog] ul li').length") == 3
        for width in (1440, 390):
            cdp.call("Emulation.setDeviceMetricsOverride", width=width, height=1000,
                     deviceScaleFactor=1, mobile=False)
            browser.wait_until(lambda: cdp.evaluate("document.documentElement.scrollWidth<=innerWidth"))
            cdp.screenshot(f"gallery-details-{width}.png")
        cdp.click("Done")
        browser.wait_until(lambda: cdp.evaluate("!document.querySelector('[role=dialog]')"))
        cdp.call("Emulation.setDeviceMetricsOverride", width=1440, height=1000,
                 deviceScaleFactor=1, mobile=False)
        print("Card/detail galleries, client-only arrows and three borrow bullets passed", flush=True)
        time.sleep(0.6)  # Let the sidebar's responsive transition finish after the narrow dialog check.
        for item in first["results"]:
            assert cdp.evaluate(f"""(() => {{
              const card=document.querySelector('.st-key-reference_card_{item["id"]}');
              const host=[...card.querySelectorAll('*')].find(e=>e.shadowRoot?.querySelector('.preview'));
              const image=(card.querySelector('.rf-preview') || host).getBoundingClientRect();
              const node=card.querySelector('[class*=st-key-card_bookmark_] button');
              const button=node.getBoundingClientRect();
              return button.width===36 && button.height===36 && button.left>=image.left &&
                button.right<=image.right && button.top>=image.top && button.bottom<=image.bottom;
            }})()""")
        cdp.screenshot("research-desktop.png")
        def navigation_geometry(key):
            return cdp.evaluate(f"""(() => {{
              const button=document.querySelector('.st-key-'+{json.dumps(key)}+' button');
              const label=button.querySelector('p');
              const style=getComputedStyle(button);
              const row=button.closest('[class*=st-key-recent_row_]');
              return {{x:label.getBoundingClientRect().left,
                background:getComputedStyle(row||button).backgroundColor,
                content_x:button.firstElementChild.getBoundingClientRect().left,
                button_x:button.getBoundingClientRect().left,
                border:style.borderLeftWidth, weight:getComputedStyle(label).fontWeight}};
            }})()""")

        active_title = navigation_geometry(f"research_{first['id']}")
        assert cdp.evaluate(f"""(() => {{
          const row=document.querySelector('.st-key-recent_row_{first['id']}');
          const title=row.querySelector('.st-key-research_{first['id']} button');
          const menu=row.querySelector('.st-key-manage_{first['id']} button');
          const a=title.getBoundingClientRect(), b=menu.getBoundingClientRect();
          const r=row.getBoundingClientRect();
          const text=title.querySelector('p').getBoundingClientRect();
          const heading=document.querySelector('.st-key-toggle_recent_research p');
          return getComputedStyle(heading).fontSize===getComputedStyle(title.querySelector('p')).fontSize &&
            getComputedStyle(row).backgroundColor==='rgb(230, 230, 230)' &&
            getComputedStyle(menu).visibility==='visible' &&
            Math.abs(a.top+a.height/2-b.top-b.height/2)<1 && r.right-b.right>=6 && b.left-a.right>=8 &&
            Math.abs(r.height-40)<1 && Math.abs(text.top+text.height/2-r.top-r.height/2)<1 &&
            row.scrollWidth<=row.clientWidth+1;
        }})()"""), cdp.evaluate(f"""(() => {{
          const row=document.querySelector('.st-key-recent_row_{first['id']}');
          return [row,...row.querySelectorAll('*')].filter(e=>e===row ||
            e.tagName==='BUTTON' || e.tagName==='P' ||
            e.scrollWidth>e.clientWidth+1 &&
              ['auto','scroll'].includes(getComputedStyle(e).overflowX)).map(e=>({{
            tag:e.tagName,key:e.className,box:e.getBoundingClientRect().toJSON(),
            scroll:e.scrollWidth,client:e.clientWidth,overflow:getComputedStyle(e).overflow
          }}));
        }})()""")
        assert cdp.evaluate("""(() => {
          const rows=[...document.querySelectorAll('[class*=st-key-recent_row_]')];
          const rects=rows.map(e=>e.getBoundingClientRect());
          return rects.every((r,i)=>i===0 || Math.abs(r.top-rects[i-1].bottom-4)<1) &&
            rows.every(row=>[row,...row.querySelectorAll('*')].every(e=>
              !['auto','scroll'].includes(getComputedStyle(e).overflowX) ||
              e.scrollWidth<=e.clientWidth+1 || e.clientHeight===0));
        })()""")
        def menu_visibility(run_id):
            return cdp.evaluate(
                f"getComputedStyle(document.querySelector('.st-key-manage_{run_id}')).visibility"
            )

        assert menu_visibility(second["id"]) == "hidden"
        assert cdp.evaluate(f"""(() => {{
          const trigger=document.querySelector('.st-key-pack_options_{original['id']} button');
          const arrow=trigger.querySelector('[data-testid=stIconMaterial]');
          const selector=document.querySelector(
            '.st-key-current_pack_choice_{original['id']}');
          return getComputedStyle(arrow.parentElement.parentElement).display==='none' &&
            !!selector.querySelector('svg');
        }})()""")
        check_management_menu(
            f"manage_{first['id']}", [f"rename_{first['id']}", f"delete_{first['id']}"],
            "research-management-menu.png",
        )
        check_management_menu(
            f"pack_options_{original['id']}",
            ["pack_manage_create", "pack_manage_rename", "pack_manage_delete"], "pack-management-menu.png",
        )
        check_arrow(f"current_pack_choice_{original['id']}")
        click_key(f"result_filters_{first['id']}")
        browser.wait_until(lambda: cdp.evaluate(
            f"!!document.querySelector('.st-key-source_filter_{first['id']} input')"
        ))
        assert not cdp.evaluate("document.body.innerText.includes('Creative Category')")
        check_arrow(f"source_filter_{first['id']}")
        click_key(f"result_filters_{first['id']}")
        assert cdp.evaluate(f"""(() => {{
          const button=document.querySelector('.st-key-research_{first['id']} button');
          const r=button.getBoundingClientRect();
          return button.contains(document.elementFromPoint(r.left+5,r.top+r.height/2));
        }})()"""), cdp.evaluate(f"""(() => {{
          let node=document.querySelector('.st-key-research_{first['id']} button');
          const ancestors=[];
          while(node) {{
            const r=node.getBoundingClientRect(), s=getComputedStyle(node);
            ancestors.push({{tag:node.tagName,key:node.className,x:r.left,width:r.width,overflow:s.overflow}});
            node=node.parentElement;
          }}
          return ancestors;
        }})()""")
        def heading_geometry():
            return cdp.evaluate("""(() => {
              const heading=document.querySelector('.st-key-recent_research_heading');
              const r=heading.querySelector('p').getBoundingClientRect();
              const scrolling=[heading,...heading.querySelectorAll('*')].filter(e=>
                ['auto','scroll'].includes(getComputedStyle(e).overflowY) &&
                e.scrollHeight>e.clientHeight+1 && e.clientWidth>0);
              return {top:r.top,height:r.height,scrollbars:scrolling.length};
            })()""")

        before_heading = heading_geometry()
        assert before_heading["scrollbars"] == 0
        heading_point = cdp.evaluate("""(() => {
          const r=document.querySelector('.st-key-recent_research_heading').getBoundingClientRect();
          return {x:r.left+10,y:r.top+r.height/2};
        })()""")
        cdp.call("Input.dispatchMouseEvent", type="mouseMoved", **heading_point)
        browser.wait_until(lambda: cdp.evaluate(
            "getComputedStyle(document.querySelector('.st-key-toggle_recent_research "
            "[data-testid=stIconMaterial]')).opacity==='1'"
        ))
        assert not cdp.evaluate("""[...document.querySelectorAll('[role=tooltip]')].some(e=>
          /expand recent research|collapse recent research/i.test(e.innerText))""")
        click_key("toggle_recent_research")
        browser.wait_until(lambda: cdp.evaluate("!document.querySelector('.st-key-recent_research')"))
        after_heading = heading_geometry()
        assert after_heading["scrollbars"] == 0
        assert abs(after_heading["top"] - before_heading["top"]) < 1
        assert after_heading["height"] == before_heading["height"]
        assert cards(4) and pack_count(3)
        cdp.screenshot("recent-research-collapsed.png")
        click_key("toggle_recent_research")
        browser.wait_until(lambda: cdp.evaluate(
            f"!!document.querySelector('.st-key-research_{first['id']}')"
        ))
        assert cards(4) and pack_count(3)
        cdp.call("Input.dispatchMouseEvent", type="mouseMoved", x=600, y=100)
        click_key(f"research_{second['id']}")
        browser.wait_until(lambda: cdp.evaluate(
            f"document.querySelector('.st-key-research_{second['id']} button')"
            "?.getAttribute('kind')==='primary'"
        ))
        inactive_title = navigation_geometry(f"research_{first['id']}")
        assert menu_visibility(first["id"]) == "hidden"
        assert menu_visibility(second["id"]) == "visible"
        assert abs(active_title["x"] - inactive_title["x"]) < 1
        assert active_title["weight"] == inactive_title["weight"]
        assert active_title["background"] == "rgb(230, 230, 230)"
        click_key(f"research_{first['id']}")
        browser.wait_until(lambda: cdp.evaluate(
            f"document.querySelector('.st-key-research_{first['id']} button')"
            "?.getAttribute('kind')==='primary'"
        ))
        inactive_favorites = navigation_geometry("open_favorites")
        new_research = navigation_geometry("sidebar_new_research")
        assert cdp.evaluate("""(() => {
          const start=document.querySelector('.st-key-sidebar_new_research button').getBoundingClientRect();
          const favorites=document.querySelector('.st-key-open_favorites button').getBoundingClientRect();
          const rows=[...document.querySelectorAll('[class*=st-key-recent_row_]')]
            .map(e=>e.getBoundingClientRect());
          return Math.abs(favorites.top-start.bottom-4)<1 &&
            Math.abs(favorites.top-start.bottom-(rows[1].top-rows[0].bottom))<1;
        })()""")
        caption_left = cdp.evaluate("""(() => {
          const labels=[...document.querySelectorAll('[data-testid=stSidebar] p')];
          const logo=document.querySelector('[data-testid=stSidebar] .rf-sidebar-logo');
          if (!logo?.complete || logo.naturalWidth!==712) throw new Error('Sidebar logo did not load');
          return [logo.getBoundingClientRect().left,
            labels.find(e=>e.innerText.trim()==='RECENT RESEARCH').getBoundingClientRect().left];
        })()""")
        assert abs(new_research["x"] - inactive_favorites["x"]) < 1
        assert all(
            abs(value["content_x"] - x) < 1
            for value in (new_research, inactive_favorites, active_title) for x in caption_left
        )
        assert all(abs(value["content_x"] - value["button_x"] - 10) < 1
                   for value in (new_research, inactive_favorites, active_title))
        assert new_research["x"] > new_research["content_x"]
        assert inactive_favorites["x"] > inactive_favorites["content_x"]
        assert new_research["border"] == "0px"
        click_key("open_favorites")
        browser.wait_until(lambda: cards(2))
        active_favorites = navigation_geometry("open_favorites")
        assert abs(active_favorites["x"] - inactive_favorites["x"]) < 1
        assert active_favorites["background"] == "rgb(230, 230, 230)"
        cdp.screenshot("sidebar-neutral-selection.png")
        click_key("sidebar_new_research")
        browser.wait_until(lambda: cdp.evaluate(
            "document.body.innerText.includes('Describe your creative idea')"
        ))
        assert navigation_geometry("sidebar_new_research")["background"] == "rgb(230, 230, 230)"
        assert all(
            abs(navigation_geometry("sidebar_new_research")["content_x"] - x) < 1 for x in caption_left
        )
        cdp.screenshot("sidebar-new-research-selected.png")
        check_arrow("brief_sources")
        click_key("open_favorites")
        browser.wait_until(lambda: cards(2))
        print("Sidebar labels stay aligned; selected research/Favorites use neutral grey", flush=True)
        click_key(f"pick_action_favorites_{favorites[1]['id']}")
        browser.wait_until(lambda: pack_count(4))
        click_key(f"favorite_favorites_{favorites[0]['id']}")
        browser.wait_until(lambda: cards(1))
        assert len(cdp.pack_state()["ids"]) == 4
        request(f"/search-runs/{first['id']}", "DELETE")
        cdp.call("Page.reload")
        unlock_workspace()
        browser.wait_until(lambda: cards(1) and pack_count(4))
        click_key(f"details_{favorites[1]['id']}")
        browser.wait_until(lambda: cdp.evaluate("document.body.innerText.includes('Why It Fits Your Brief')"))
        cdp.click("Done")
        browser.wait_until(lambda: cdp.evaluate("!document.querySelector('[role=dialog]')"))
        print("Bookmarks, Favorites cards and details survive deleting the original research", flush=True)
        for width in (1440, 1024, 768, 390):
            cdp.call(
                "Emulation.setDeviceMetricsOverride",
                width=width,
                height=1000,
                deviceScaleFactor=1,
                mobile=False,
            )
            time.sleep(0.3)
            assert cdp.evaluate("document.documentElement.scrollWidth<=innerWidth")
            cdp.screenshot(f"favorites-{width}.png")
        cdp.call(
            "Emulation.setDeviceMetricsOverride", width=1440, height=1000, deviceScaleFactor=1, mobile=False
        )
        click_key(f"pack_options_{original['id']}")
        click_key("pack_manage_create")
        browser.wait_until(lambda: cdp.evaluate("!!document.querySelector('[role=dialog] input')"))
        check_pack_dialog("create")
        cdp.evaluate("document.querySelector('[role=dialog] input').focus()")
        cdp.call("Input.insertText", text="Favorites Browser Pack")
        cdp.call("Input.dispatchKeyEvent", type="keyDown", key="Tab", code="Tab", windowsVirtualKeyCode=9)
        time.sleep(0.3)
        click_key("submit_pack_management")
        browser.wait_until(lambda: request("/library")["current_pack"]["name"] == "Favorites Browser Pack")
        browser.wait_until(lambda: pack_count(0))
        click_key(f"pick_action_favorites_{favorites[1]['id']}")
        browser.wait_until(lambda: pack_count(1))
        new_pack = request("/library")["current_pack"]
        cdp.screenshot("pack-created-from-favorites.png")
        click_key(f"pack_options_{new_pack['id']}")
        click_key("pack_manage_rename")
        browser.wait_until(lambda: cdp.evaluate("!!document.querySelector('[role=dialog] input')"))
        check_pack_dialog("rename")
        click_key("cancel_pack_management")
        browser.wait_until(lambda: cdp.evaluate("!document.querySelector('[role=dialog]')"))
        select = f".st-key-current_pack_choice_{new_pack['id']} [role=combobox]"
        point = cdp.evaluate(f"""(() => {{
          const r=document.querySelector({json.dumps(select)}).getBoundingClientRect();
          return {{x:r.left+r.width/2,y:r.top+r.height/2}};
        }})()""")
        cdp.call("Input.dispatchMouseEvent", type="mousePressed", button="left", clickCount=1, **point)
        cdp.call("Input.dispatchMouseEvent", type="mouseReleased", button="left", clickCount=1, **point)
        pack_names = [item["name"] for item in request("/library")["packs"]]
        original_label = (
            original["name"] if pack_names.count(original["name"]) == 1
            else f"{original['name']} · {original['id'][:4]}"
        )
        option = (
            "[...document.querySelectorAll('[role=option]')]"
            f".find(e=>e.innerText.trim()==={json.dumps(original_label)})"
        )
        browser.wait_until(lambda: cdp.evaluate(f"!!({option})"))
        cdp.evaluate(option + ".click()")
        browser.wait_until(lambda: pack_count(4))
        cdp.drag_pack(0, 3)
        displayed = cdp.pack_state()["ids"]
        browser.wait_until(lambda: request("/library")["current_pack"]["ordered_ids"] == displayed)
        cdp.click("Export Reference Pack")
        browser.wait_until(
            lambda: cdp.evaluate("document.body.innerText.includes('Download Reference Pack (.pptx)')")
        )
        repo = Repository(config.data_dir / "reference_finder.sqlite3")
        with repo.connection() as conn:
            record = json.loads(
                conn.execute(
                    "SELECT payload FROM records WHERE kind='export' ORDER BY rowid DESC LIMIT 1"
                ).fetchone()["payload"]
            )
        content = request(f"/exports/{record['id']}/download", binary=True)
        pack = request("/library")["current_pack"]
        for slide, reference_id in zip(slides_from_bytes(content)[2:-1], displayed, strict=True):
            assert pack["references"][reference_id]["reference"]["title"].upper() in slide_text(slide)
        (ARTIFACTS / "cross-research-pack.pptx").write_bytes(content)
        (ARTIFACTS / "report.json").write_text(
            json.dumps(
                {
                    "favorites_survive_research_deletion": True,
                    "pack_order": displayed,
                    "created_pack_id": new_pack["id"],
                    "widths": [1440, 1024, 768, 390],
                    "openai_calls": 0,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(
            f"Pack creation, switching, actual drag and exact PPTX order passed. Artifacts: {ARTIFACTS}",
            flush=True,
        )
    except Exception:
        if cdp:
            try:
                cdp.screenshot("failure.png")
                (ARTIFACTS / "failure-dom.json").write_text(
                    json.dumps(
                        cdp.evaluate("""({
                  pack: document.querySelector('.st-key-pack_panel')?.outerHTML,
                  options: [...document.querySelectorAll('[role=option]')].map(e=>e.innerText)
                })""")
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass
        print(f"Browser check artifacts: {ARTIFACTS}", flush=True)
        raise
    finally:
        if cdp:
            cdp.connection.__exit__(None, None, None)
        for process in reversed(processes):
            if process.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        print(f"Test process {process.pid} could not be stopped", flush=True)
        for log in logs:
            log.close()
        profile = ARTIFACTS / "chrome-profile"
        if profile.exists():
            if not ARTIFACTS.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
                raise RuntimeError("Unexpected temporary artifact path")
            if profile.resolve().parent != ARTIFACTS.resolve():
                raise RuntimeError("Unexpected Chrome profile path")
            shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    main()
