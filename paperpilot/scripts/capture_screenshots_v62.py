"""Browser QA + screenshots for the v6.2 custom-model settings drawer.

Owns an isolated data directory (``out/screens-v62/data``), starts a local
OpenAI-compatible stub on 127.0.0.1, then drives the real UI:

* the offline callout and the persistent topbar entry are visible on load
* choose a provider, paste a key, 测试连接 performs a real HTTP round trip
* 保存并启用 flips the mode badge to 在线增强模式 and hides the callout
* the raw key never appears in the DOM; only the mask does
* per-purpose routes round-trip through /api/llm/settings
* a draft semantic audit then calls the configured model (visible in /api/llm/audit)
* 清除配置 returns the app to 离线规则模式
* assert 0 console errors, 0 failed requests and 0 overflow at 1600px / 390px

Mirrors the bootstrapping of ``capture_screenshots_v20.py`` so the scripts can
run side by side without touching each other's data.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import http.server
from pathlib import Path

import requests
import truststore
from playwright.sync_api import sync_playwright

truststore.inject_into_ssl()

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
WORK = ROOT / "out" / "screens-v62"
DATA = WORK / "data"

TITLE = "面向存算一体的低功耗 ADC 研究"
QUESTION = "如何在保持精度的同时降低存内计算 ADC 的数据搬运与校准开销？"
GOAL = "存算一体 ADC 的低功耗架构与校准开销"
STUB_KEY = "sk-qa-stub-4f7k"
STUB_MODEL = "qa-stub-chat"
STUB_ROUTE_MODEL = "qa/qa-stub-fast"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class StubHandler(http.server.BaseHTTPRequestHandler):
    calls: list[dict] = []

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            payload = {}
        StubHandler.calls.append({
            "path": self.path,
            "auth": self.headers.get("authorization") or "",
            "model": payload.get("model"),
        })
        body = json.dumps({
            "model": STUB_MODEL,
            "choices": [{"message": {"content": "ok from stub"}}],
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return None


def _start_stub() -> tuple[http.server.ThreadingHTTPServer, int]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, int(server.server_address[1])


def _start_server(port: int) -> subprocess.Popen:
    DATA.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PAPERPILOT_DATA_DIR"] = str(DATA)
    env["PAPERPILOT_ALLOW_PRIVATE_LLM"] = "1"
    env["PAPERPILOT_LLM_SETTINGS_FILE"] = str(DATA / "llm_settings.json")
    env["PAPERPILOT_LLM_SETTINGS_SECRETS_FILE"] = str(DATA / "llm_settings.secrets.json")
    env["PAPERPILOT_LLM_PERSIST_KEY"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PAPERPILOT_LLM_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    log = (WORK / "server.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 45
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited early:\n{(WORK / 'server.log').read_text(encoding='utf-8')[-2000:]}")
        try:
            if requests.get(base + "/api/health", timeout=2).status_code == 200:
                return process
        except requests.RequestException:
            time.sleep(0.4)
    raise RuntimeError("server did not become healthy")


def _wait_run(session: requests.Session, base: str, run_id: str, statuses: set[str], timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    run: dict = {}
    while time.time() < deadline:
        run = session.get(f"{base}/api/agent/runs/{run_id}", timeout=30).json()["run"]
        if run.get("status") in statuses:
            return run
        time.sleep(0.3)
    raise RuntimeError(f"run {run_id} stuck in {run.get('status')}")


def seed(session: requests.Session, base: str) -> dict:
    project = session.post(f"{base}/api/projects", json={
        "title": TITLE, "question": QUESTION,
        "scope": "CIM 架构、SAR ADC 与数字校准", "tags": ["存算一体", "ADC"],
    }, timeout=30).json()["project"]

    run = session.post(f"{base}/api/agent/runs", json={
        "goal": GOAL, "mode": "auto", "project_id": project["id"], "max_papers": 4,
    }, timeout=60).json()["run"]
    for _ in range(8):
        run = _wait_run(session, base, run["id"], {"completed", "failed", "awaiting_approval"})
        if run["status"] != "awaiting_approval":
            break
        session.post(f"{base}/api/agent/runs/{run['id']}/action", json={"action": "approve"}, timeout=60)
    if run["status"] != "completed":
        raise RuntimeError(f"run did not complete: {run.get('status')}")

    memory = session.get(f"{base}/api/projects/{project['id']}/memory?kind=evidence&limit=100",
                         timeout=30).json()["items"]
    anchored = [item["id"] for item in memory if item.get("verified")][:2]
    draft = session.get(f"{base}/api/projects/{project['id']}/draft", timeout=30).json()["draft"]
    session.put(f"{base}/api/projects/{project['id']}/draft", json={
        "revision": draft["revision"],
        "title": "存算一体低功耗 ADC 综述草稿",
        "citation_style": "gb7714",
        "sections": [
            {"id": "sec-001", "title": "引言", "blocks": [
                {"id": "blk-001", "type": "paragraph", "status": "checked",
                 "text": "存内计算把乘累加搬到存储阵列附近，数据搬运开销因此下降。",
                 "evidence_ids": anchored, "citation_keys": []},
                {"id": "blk-002", "type": "paragraph", "status": "draft",
                 "text": "该方法相比传统方案能耗降低 42%，吞吐提升 1.8 倍。",
                 "evidence_ids": [], "citation_keys": []},
            ]},
        ],
    }, timeout=30)
    return {"project_id": project["id"], "run_id": run["id"], "evidence": len(memory)}


def main() -> int:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    stub, stub_port = _start_stub()
    port = _free_port()
    process = _start_server(port)
    base = f"http://127.0.0.1:{port}"
    stub_base = f"http://127.0.0.1:{stub_port}/v1"
    session = requests.Session()
    console_errors: list[str] = []
    failed_requests: list[str] = []
    try:
        seeded = seed(session, base)
        print("seeded:", seeded, flush=True)
        print("offline mode:", session.get(f"{base}/api/health", timeout=30).json()["mode"], flush=True)

        overflow_values: list[int] = []
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="msedge", headless=True)
            except Exception:
                browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1600, "height": 1300})
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda exc: console_errors.append(str(exc)))
            page.on("dialog", lambda dialog: dialog.accept())

            def _note_failure(r):
                if r.status < 400:
                    return
                body = ""
                try:
                    if "json" in (r.headers.get("content-type") or ""):
                        body = r.text()[:200]
                except Exception:
                    body = ""
                failed_requests.append(f"{r.status} {r.request.method} {r.url} {body}")
            page.on("response", _note_failure)

            page.goto(base, wait_until="networkidle")
            badge = page.locator("#modeBadge").inner_text()
            callout_visible = page.locator("#modelCallout").is_visible()
            print("badge:", badge, "| callout visible:", callout_visible, flush=True)
            if "离线" not in badge or not callout_visible:
                raise RuntimeError(f"expected offline badge and callout, got {badge} / {callout_visible}")

            # --- configure a model through the UI ---------------------------
            page.locator("#modelBtn").click()
            drawer = page.locator("[data-model-drawer]")
            drawer.wait_for(timeout=20000)
            chips = page.locator("[data-model-provider]").count()
            print("provider chips:", chips, flush=True)
            if chips < 6:
                raise RuntimeError(f"expected >=6 provider presets, got {chips}")

            page.locator('[data-model-provider="custom"]').click()

            def type_into(selector: str, text: str) -> str:
                # Type real keystrokes: v6.2.2 fixed the global "/" shortcut eating
                # slashes in these fields, and fill() would hide the regression.
                node = page.locator(selector)
                node.click()
                node.fill("")
                node.press_sequentially(text, delay=8)
                value = node.input_value()
                if value != text:
                    raise RuntimeError(f"typing into {selector} lost characters: {value!r} != {text!r}")
                return value

            if page.locator('[data-model-field="base_url"]').input_value() != "":
                raise RuntimeError("choosing 自定义 must clear the previous provider's URL")
            type_into('[data-model-field="base_url"]', stub_base)
            type_into('[data-model-field="model"]', STUB_MODEL)
            type_into('[data-model-field="api_key"]', STUB_KEY)
            print("typed fields ok (slashes survive):", stub_base, flush=True)
            page.locator("[data-model-test]").click()
            page.wait_for_function(
                "() => (document.querySelector('[data-model-status]')?.textContent || '').includes('连接成功')",
                timeout=40000,
            )
            print("test status:", page.locator("[data-model-status]").inner_text(), flush=True)
            if not StubHandler.calls:
                raise RuntimeError("stub received no request")

            type_into('[data-model-route="assistant"]', STUB_ROUTE_MODEL)
            page.locator("[data-model-save]").click()
            page.wait_for_function(
                "() => (document.querySelector('[data-model-status]')?.textContent || '').includes('已启用')",
                timeout=40000,
            )
            print("save status:", page.locator("[data-model-status]").inner_text(), flush=True)
            page.locator("[data-model-drawer]").screenshot(path=str(DOCS / "screenshot-model.png"))

            leaked = page.evaluate(
                "(key) => Array.from(document.querySelectorAll('input')).some((node) => node.value === key)"
                " || document.body.innerText.includes(key)",
                STUB_KEY,
            )
            mask_seen = STUB_KEY[-4:] in page.locator(".ml-pills").inner_text()
            print("key leaked in DOM:", leaked, "| mask shown:", mask_seen, flush=True)
            if leaked:
                raise RuntimeError("raw API key found in the DOM")

            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(500)
            overflow_values.append(page.evaluate(
                "() => document.documentElement.scrollWidth - window.innerWidth"))
            if overflow_values[-1] > 0:
                print("overflow elements:", page.evaluate("""() => {
                  const limit = window.innerWidth;
                  const out = [];
                  document.querySelectorAll('*').forEach((node) => {
                    const rect = node.getBoundingClientRect();
                    const label = node.tagName + '.' + (node.getAttribute('class') || '');
                    if (rect.right > limit + 1 && rect.width > 0) {
                      out.push(label.slice(0, 48) + ' -> ' + Math.round(rect.right));
                    }
                  });
                  return out.slice(0, 6);
                }"""), flush=True)
            page.locator("[data-model-drawer]").screenshot(path=str(DOCS / "screenshot-model-mobile.png"))
            page.set_viewport_size({"width": 1600, "height": 1300})

            page.evaluate("() => window.PaperPilotModel?.close()")
            page.wait_for_function("() => document.querySelector('#modeBadge')?.textContent.includes('在线')",
                                   timeout=30000)
            badge_online = page.locator("#modeBadge").inner_text()
            callout_hidden = page.locator("#modelCallout").is_hidden()
            topbar_text = page.locator("#modelBtn").inner_text()
            print("badge after save:", badge_online, "| callout hidden:", callout_hidden,
                  "| topbar:", topbar_text, flush=True)
            if not callout_hidden:
                raise RuntimeError("offline callout still visible after configuring a model")

            # --- the configured model is what the app actually calls --------
            page.locator('[data-tab="projects"]').click()
            page.locator(f'[data-project-open="{seeded["project_id"]}"]').wait_for(timeout=20000)
            page.locator(f'[data-project-open="{seeded["project_id"]}"]').click()
            page.locator('[data-project-tab="draft"]').click()
            page.locator("[data-draft-semantic]").wait_for(timeout=20000)
            before = len(StubHandler.calls)
            page.locator("[data-draft-semantic]").click()
            deadline = time.time() + 45
            while time.time() < deadline and len(StubHandler.calls) <= before:
                time.sleep(0.4)
            audit = session.get(f"{base}/api/llm/audit", timeout=30).json()["summary"]
            print("stub calls now:", len(StubHandler.calls), "| llm summary:",
                  {k: audit[k] for k in ("calls", "ok", "failed")}, "| purposes:", audit.get("by_purpose"),
                  flush=True)
            if len(StubHandler.calls) <= before:
                raise RuntimeError("draft semantic audit did not reach the configured model")
            if audit.get("calls", 0) < 1:
                raise RuntimeError("llm audit recorded no call after enabling the model")
            audit_calls = session.get(f"{base}/api/llm/audit?limit=20", timeout=30).json()["calls"]
            models_used = {str(item.get("model") or "") for item in audit_calls}
            print("audit models:", sorted(models_used), "| purposes:", audit.get("by_purpose"), flush=True)
            if STUB_MODEL not in models_used:
                raise RuntimeError(f"configured model never reached the audit trail: {sorted(models_used)}")

            settings = session.get(f"{base}/api/llm/settings", timeout=30).json()["settings"]
            print("routes:", settings["routes"], "| masked:", settings["masked_key"],
                  "| model:", settings["model"], flush=True)
            if settings["routes"] != {"assistant": STUB_ROUTE_MODEL}:
                raise RuntimeError(f"per-purpose route missing: {settings['routes']}")
            if STUB_KEY in json.dumps(settings):
                raise RuntimeError("settings payload leaked the raw key")
            last = StubHandler.calls[-1]
            print("stub last call:", last, flush=True)
            if last["auth"] != "Bearer " + STUB_KEY:
                raise RuntimeError(f"stub missing bearer key: {last}")
            if last["model"] not in {STUB_MODEL, STUB_ROUTE_MODEL}:
                raise RuntimeError(f"unexpected model sent to the stub: {last['model']}")
            route_hit = any(item["model"] == STUB_ROUTE_MODEL for item in StubHandler.calls[before:])
            print("assistant route model used:", route_hit, flush=True)

            # reopen the drawer so the call statistics are visible
            page.locator('[data-tab="read"]').click()
            page.locator("#modelBtn").click()
            page.locator("[data-model-drawer]").wait_for(timeout=20000)
            page.wait_for_function(
                "() => !/调用 0/.test(document.querySelector('.ml-stats')?.textContent || '')",
                timeout=20000,
            )
            print("call stats:", page.locator(".ml-stats").inner_text(), flush=True)
            page.locator("[data-model-drawer]").screenshot(path=str(DOCS / "screenshot-model-usage.png"))
            page.evaluate("() => window.PaperPilotModel?.close()")

            # per-purpose routing must reach the wire, not just the settings store
            routed_before = len(StubHandler.calls)
            ask = session.post(f"{base}/api/projects/{seeded['project_id']}/assistant",
                               json={"message": "存算一体低功耗 ADC 的校准开销有哪些证据支持？"}, timeout=90)
            routed = StubHandler.calls[routed_before:]
            reply_mode = ((ask.json() or {}).get("reply") or {}).get("mode") if ask.ok else ""
            print("assistant ask:", ask.status_code, "| mode:", reply_mode,
                  "| stub calls:", routed[:2], flush=True)
            if ask.status_code != 200 or not routed:
                raise RuntimeError(f"assistant ask did not reach the stub: {ask.status_code} {reply_mode}")
            if routed[0]["model"] != STUB_ROUTE_MODEL:
                raise RuntimeError(f"assistant ignored the routed model: {routed[0]}")
            if reply_mode not in {"llm", "llm_degraded"}:
                raise RuntimeError(f"assistant did not use the configured model: {reply_mode}")

            # --- back to offline --------------------------------------------
            page.locator("#modelBtn").click()
            page.locator("[data-model-drawer]").wait_for(timeout=20000)
            page.locator("[data-model-clear]").click()
            page.wait_for_function(
                "() => (document.querySelector('[data-model-status]')?.textContent || '').includes('已清除')",
                timeout=40000,
            )
            page.evaluate("() => window.PaperPilotModel?.close()")
            page.wait_for_function("() => document.querySelector('#modeBadge')?.textContent.includes('离线')",
                                   timeout=30000)
            print("badge after clear:", page.locator("#modeBadge").inner_text(),
                  "| callout visible:", page.locator("#modelCallout").is_visible(), flush=True)
            if (DATA / "llm_settings.json").exists() or (DATA / "llm_settings.secrets.json").exists():
                raise RuntimeError("clear left settings files on disk")

            print("mobile overflow:", overflow_values, flush=True)
            print("console_errors:", console_errors, flush=True)
            print("failed_requests:", failed_requests, flush=True)
            browser.close()

        if any(overflow_values) or console_errors or failed_requests:
            print("FAIL: qa signals", overflow_values, console_errors[:3], failed_requests[:3])
            return 1
        print("model settings drawer: PASS")
        print("screenshots: screenshot-model.png, screenshot-model-mobile.png, screenshot-model-usage.png")
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        stub.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
