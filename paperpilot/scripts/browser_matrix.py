"""Run a small Copilot smoke test on Chromium, Firefox and WebKit.

Missing browser engines are recorded as ``skipped`` rather than failing the
run; at least one engine must pass. The script owns a temporary data directory
and never touches the shipped project data.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
import truststore
from playwright.sync_api import sync_playwright

truststore.inject_into_ssl()

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_server(port: int, data_dir: Path) -> subprocess.Popen:
    data_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PAPERPILOT_DATA_DIR"] = str(data_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PAPERPILOT_LLM_API_KEY", None)
    log = (data_dir.parent / "server.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
        ],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 40
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("server exited early")
        try:
            if requests.get(base + "/api/health", timeout=2).status_code == 200:
                return process
        except requests.RequestException:
            time.sleep(0.3)
    raise RuntimeError("server did not become healthy")


def _seed(base: str) -> str:
    project = requests.post(
        f"{base}/api/projects",
        json={"title": "浏览器矩阵项目", "question": "Copilot 在哪些浏览器可用？"},
        timeout=30,
    ).json()["project"]
    return project["id"]


def _engine_launch(playwright, name: str):
    if name == "chromium":
        try:
            return playwright.chromium.launch(channel="msedge", headless=True)
        except Exception:
            return playwright.chromium.launch(headless=True)
    if name == "firefox":
        return playwright.firefox.launch(headless=True)
    if name == "webkit":
        return playwright.webkit.launch(headless=True)
    raise ValueError(name)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="paperpilot-browser-"))
    port = _free_port()
    process = _start_server(port, tmp / "data")
    base = f"http://127.0.0.1:{port}"
    results = []
    try:
        _seed(base)
        with sync_playwright() as playwright:
            for name in ("chromium", "firefox", "webkit"):
                try:
                    browser = _engine_launch(playwright, name)
                except Exception as exc:  # noqa: BLE001
                    results.append({"engine": name, "status": "skipped", "reason": type(exc).__name__})
                    print(f"[SKIP] {name}: {type(exc).__name__}", flush=True)
                    continue
                errors: list[str] = []
                try:
                    page = browser.new_page(viewport={"width": 1400, "height": 1000})
                    page.on(
                        "console",
                        lambda msg: errors.append(msg.text) if msg.type == "error" else None,
                    )
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.set_default_timeout(45000)
                    page.goto(base, wait_until="domcontentloaded")
                    page.wait_for_selector(".result-card", timeout=40000)
                    page.locator("#tabs [data-tab='projects']").click()
                    page.wait_for_selector(".project-card", timeout=30000)
                    page.locator(".project-card", has_text="浏览器矩阵项目").first.click()
                    page.wait_for_selector(".project-hero", timeout=30000)
                    page.locator("[data-project-tab='assistant']").click()
                    page.wait_for_selector(".assistant-form", timeout=30000)
                    page.fill("[data-assistant-input]", "项目进展如何？")
                    page.click("[data-assistant-send]")
                    page.wait_for_selector(".assistant-msg.assistant", timeout=45000)
                    page.wait_for_timeout(300)
                    desktop_text = page.locator(".assistant-msg.assistant").last.inner_text()
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.wait_for_timeout(300)
                    overflow = page.evaluate(
                        "() => document.documentElement.scrollWidth - window.innerWidth"
                    )
                    ok = bool(desktop_text.strip()) and not errors and int(overflow) == 0
                    results.append(
                        {
                            "engine": name,
                            "status": "passed" if ok else "failed",
                            "console_errors": errors,
                            "overflow": int(overflow),
                            "chars": len(desktop_text),
                        }
                    )
                    print(
                        f"[{'PASS' if ok else 'FAIL'}] {name}: "
                        f"errors={len(errors)} overflow={overflow} chars={len(desktop_text)}",
                        flush=True,
                    )
                finally:
                    browser.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    passed = [item for item in results if item["status"] == "passed"]
    failed = [item for item in results if item["status"] == "failed"]
    print(json.dumps(results, ensure_ascii=False, indent=2), flush=True)
    if failed or not passed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
