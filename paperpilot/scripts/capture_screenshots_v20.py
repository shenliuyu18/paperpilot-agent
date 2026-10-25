"""Capture the v2.0 research-workspace screenshots against a seeded local server.

The script owns its data directory (``out/screens-v20/data``) so the shipped
library, uploads, agent runs and project memory are never touched, seeds one
project with two runs plus a draft, then drives the real UI with Playwright.
Screenshots are written losslessly into ``docs/``.
"""

from __future__ import annotations

from _console import make_console_safe

make_console_safe()

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests
import truststore
from playwright.sync_api import sync_playwright

truststore.inject_into_ssl()

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
WORK = ROOT / "out" / "screens-v20"
DATA = WORK / "data"

TITLE = "面向存算一体的低功耗 ADC 研究"
QUESTION = "如何在保持精度的同时降低存内计算 ADC 的数据搬运与校准开销？"
GOAL_ONE = "存内计算 ADC 的低功耗架构与校准开销"
GOAL_TWO = "验证 SAR ADC 数字校准算法的面积与能耗代价"

SHOTS = [
    "screenshot-projects.png",
    "screenshot-project-overview.png",
    "screenshot-run-compare.png",
    "screenshot-draft-studio.png",
    "screenshot-draft-audit.png",
    "screenshot-agent-live.png",
]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_server(port: int) -> subprocess.Popen:
    DATA.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PAPERPILOT_DATA_DIR"] = str(DATA)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PAPERPILOT_LLM_API_KEY", None)
    log = (WORK / "server.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
        ],
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
    session.post(
        f"{base}/api/projects",
        json={
            "title": "SAR ADC 后台校准的开销边界",
            "question": "后台校准算法额外消耗的面积与能耗，在什么规模下仍然划算？",
            "scope": "校准算法与面积能耗折衷",
            "tags": ["SAR ADC", "校准"],
        },
        timeout=30,
    )
    project = session.post(
        f"{base}/api/projects",
        json={
            "title": TITLE,
            "question": QUESTION,
            "scope": "CIM 架构、SAR ADC 与数字校准",
            "constraints": ["优先开放获取论文", "需要可复现的能耗数据"],
            "tags": ["存算一体", "ADC"],
        },
        timeout=30,
    ).json()["project"]

    first = session.post(
        f"{base}/api/agent/runs",
        json={"goal": GOAL_ONE, "mode": "auto", "project_id": project["id"], "max_papers": 4},
        timeout=60,
    ).json()["run"]
    for _ in range(8):
        first = _wait_run(session, base, first["id"], {"completed", "failed", "awaiting_approval"})
        if first["status"] != "awaiting_approval":
            break
        session.post(f"{base}/api/agent/runs/{first['id']}/action", json={"action": "approve"}, timeout=60)
    if first["status"] != "completed":
        raise RuntimeError(f"first run did not complete: {first.get('status')}")

    second = session.post(
        f"{base}/api/agent/runs",
        json={
            "goal": GOAL_TWO, "mode": "checkpoint", "project_id": project["id"],
            "seed_memory": True, "max_papers": 4,
        },
        timeout=60,
    ).json()["run"]
    second = _wait_run(session, base, second["id"], {"awaiting_approval", "completed", "failed"})

    memory = session.get(f"{base}/api/projects/{project['id']}/memory?kind=evidence&limit=100", timeout=30).json()["items"]
    papers = session.get(f"{base}/api/projects/{project['id']}/memory?kind=papers&limit=100", timeout=30).json()["items"]
    anchored = [item["id"] for item in memory if item.get("verified")][:2]
    draft = session.get(f"{base}/api/projects/{project['id']}/draft", timeout=30).json()["draft"]
    payload = {
        "revision": draft["revision"],
        "title": "存算一体低功耗 ADC 综述草稿",
        "citation_style": "numeric",
        "sections": [
            {
                "id": "sec-001",
                "title": "引言",
                "blocks": [
                    {
                        "id": "blk-001",
                        "type": "paragraph",
                        "status": "checked",
                        "text": "存内计算把乘累加搬到存储阵列附近，数据搬运开销因此下降。",
                        "evidence_ids": anchored,
                        "citation_keys": [],
                    },
                    {
                        "id": "blk-002",
                        "type": "paragraph",
                        "status": "draft",
                        "text": "该方法相比传统方案能耗降低 42%，吞吐提升 1.8 倍，并且在全部工艺角与温度组合下都保持稳定。",
                        "evidence_ids": [],
                        "citation_keys": [],
                    },
                ],
            },
            {
                "id": "sec-002",
                "title": "相关工作",
                "blocks": [
                    {
                        "id": "blk-003",
                        "type": "bullet",
                        "status": "needs_evidence",
                        "text": "混合量化方案在精度与吞吐之间取得平衡。",
                        "evidence_ids": [],
                        "citation_keys": [],
                    },
                ],
            },
        ],
    }
    saved = session.put(f"{base}/api/projects/{project['id']}/draft", json=payload, timeout=30).json()["draft"]
    if papers:
        session.post(
            f"{base}/api/projects/{project['id']}/draft/citations",
            json={"paper_id": papers[0]["id"], "revision": saved["revision"]},
            timeout=30,
        )
    session.post(f"{base}/api/projects/{project['id']}/draft/versions", json={"label": "截图前基线"}, timeout=30)
    return {
        "project_id": project["id"],
        "first_run": first["id"],
        "second_run": second["id"],
        "second_status": second["status"],
        "evidence": len(memory),
        "papers": len(papers),
    }


def main() -> int:
    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    DOCS.mkdir(exist_ok=True)
    for name in SHOTS:
        stale = DOCS / name
        if stale.exists():
            stale.unlink()
    port = _free_port()
    process = _start_server(port)
    base = f"http://127.0.0.1:{port}"
    console_errors: list[str] = []
    try:
        session = requests.Session()
        info = seed(session, base)
        print("seeded:", info, flush=True)

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="msedge", headless=True)
            except Exception:
                browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1600, "height": 1200})
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda error: console_errors.append(str(error)))
            page.set_default_timeout(60000)

            page.goto(base, wait_until="domcontentloaded")
            page.wait_for_selector(".result-card", timeout=40000)
            page.locator("#tabs [data-tab='projects']").click()
            page.wait_for_selector(".project-card", timeout=30000)
            page.wait_for_timeout(400)
            page.screenshot(path=str(DOCS / "screenshot-projects.png"), full_page=True)

            page.locator(".project-card", has_text=TITLE).first.click()
            page.wait_for_selector(".project-hero", timeout=30000)
            page.wait_for_timeout(500)
            page.screenshot(path=str(DOCS / "screenshot-project-overview.png"), full_page=True)

            page.locator("[data-project-tab='runs']").click()
            page.wait_for_selector("[data-compare-run]", timeout=30000)
            picks = page.locator("[data-compare-run]")
            for index in range(min(2, picks.count())):
                picks.nth(index).check()
            page.wait_for_timeout(200)
            page.locator("[data-project-compare]").click()
            page.wait_for_selector(".compare-table", timeout=30000)
            page.wait_for_timeout(400)
            page.screenshot(path=str(DOCS / "screenshot-run-compare.png"), full_page=True)

            page.locator("[data-project-tab='draft']").click()
            page.wait_for_selector(".draft-studio", timeout=30000)
            page.wait_for_selector(".draft-block", timeout=30000)
            page.wait_for_timeout(600)
            page.screenshot(path=str(DOCS / "screenshot-draft-studio.png"), full_page=True)

            page.locator("[data-draft-audit]").click()
            page.wait_for_selector(".draft-issue", timeout=30000)
            page.wait_for_timeout(400)
            page.set_viewport_size({"width": 1600, "height": 1900})
            page.wait_for_timeout(400)
            page.locator(".draft-side .card").last.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            page.screenshot(path=str(DOCS / "screenshot-draft-audit.png"))
            page.set_viewport_size({"width": 1600, "height": 1200})

            page.locator("[data-project-tab='runs']").click()
            page.wait_for_selector("[data-open-run]", timeout=30000)
            live_goal = page.locator(f"[data-open-run='{info['second_run']}']")
            (live_goal if live_goal.count() else page.locator("[data-open-run]").first).click()
            page.wait_for_selector("#agent-live-bar", timeout=30000)
            page.wait_for_timeout(1200)
            page.screenshot(path=str(DOCS / "screenshot-agent-live.png"), full_page=True)
            live_state = page.locator("#agent-live-bar").inner_text().replace("\n", " ")
            print("live bar:", live_state[:160], flush=True)

            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(base, wait_until="domcontentloaded")
            page.wait_for_selector(".result-card", timeout=40000)
            page.locator("#tabs [data-tab='projects']").click()
            page.wait_for_selector(".project-card", timeout=30000)
            page.wait_for_timeout(600)
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth - window.innerWidth"
            )
            print("mobile 390px overflow:", overflow, flush=True)

            page.locator(".project-card", has_text=TITLE).first.click()
            page.wait_for_selector(".project-hero", timeout=30000)
            page.locator("[data-project-tab='draft']").click()
            page.wait_for_selector(".draft-studio", timeout=30000)
            page.wait_for_timeout(800)
            draft_overflow = page.evaluate(
                "() => document.documentElement.scrollWidth - window.innerWidth"
            )
            print("mobile 390px draft overflow:", draft_overflow, flush=True)
            browser.close()

        print("console_errors:", console_errors, flush=True)
        if console_errors:
            return 1
        if int(overflow) != 0 or int(draft_overflow) != 0:
            return 1
        missing = [name for name in SHOTS if not (DOCS / name).is_file()]
        if missing:
            print("missing screenshots:", missing, flush=True)
            return 1
        for name in SHOTS:
            print(f"  {name}: {(DOCS / name).stat().st_size:,} bytes", flush=True)
        print("screenshots: PASS", flush=True)
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
