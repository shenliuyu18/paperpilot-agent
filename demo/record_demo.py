from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8765"
OUT = ROOT / "out"
RAW = OUT / "raw"
RAW.mkdir(parents=True, exist_ok=True)
for old in RAW.glob("*.webm"):
    old.unlink(missing_ok=True)

TIMELINE = json.loads((ROOT / "demo_timeline.json").read_text(encoding="utf-8"))
SEGMENTS = TIMELINE["segments"]

OVERLAY = r"""
() => {
  const style = document.createElement('style');
  style.textContent = `
    #__cursor {
      position: fixed; left: 0; top: 0; width: 22px; height: 22px;
      border: 2px solid #4ce3f7; border-radius: 50%;
      box-shadow: 0 0 0 4px rgba(76,227,247,.12), 0 0 18px rgba(76,227,247,.55);
      transform: translate(-50%,-50%); pointer-events: none; z-index: 1000000;
      transition: left .04s linear, top .04s linear, transform .12s ease, background .12s ease;
      background: rgba(76,227,247,.08);
    }
    #__cursor.pulse { transform: translate(-50%,-50%) scale(1.55); background: rgba(76,227,247,.28); }
    #__caption {
      position: fixed; left: 50%; bottom: 26px; transform: translateX(-50%);
      max-width: min(1240px, calc(100vw - 100px));
      background: rgba(3,9,17,.91); border: 1px solid rgba(76,227,247,.22);
      color: #edf7ff; border-radius: 14px; padding: 11px 18px;
      font-family: "Microsoft YaHei", "Segoe UI", sans-serif; font-size: 19px;
      line-height: 1.55; text-align: center; letter-spacing: .1px;
      box-shadow: 0 18px 50px rgba(0,0,0,.45); pointer-events: none; z-index: 999999;
    }
    #__badge {
      position: fixed; top: 16px; left: 50%; transform: translateX(-50%);
      background: rgba(3,9,17,.82); border: 1px solid rgba(76,227,247,.25);
      color: #9fdcf4; border-radius: 999px; padding: 5px 13px;
      font-family: "Microsoft YaHei", "Segoe UI", sans-serif; font-size: 12px;
      letter-spacing: .5px; pointer-events: none; z-index: 999999;
    }
  `;
  document.head.appendChild(style);
  const cursor = document.createElement('div'); cursor.id = '__cursor';
  const caption = document.createElement('div'); caption.id = '__caption';
  const badge = document.createElement('div'); badge.id = '__badge';
  badge.textContent = 'PaperPilot · 产品演示';
  document.body.append(cursor, caption, badge);
  document.addEventListener('mousemove', (event) => {
    cursor.style.left = event.clientX + 'px';
    cursor.style.top = event.clientY + 'px';
  });
  document.addEventListener('mousedown', () => {
    cursor.classList.add('pulse');
    setTimeout(() => cursor.classList.remove('pulse'), 160);
  });
}
"""


def wait_until(target: float) -> None:
    delay = target - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def set_caption(page: Page, content: str) -> None:
    page.evaluate("text => { const el = document.querySelector('#__caption'); if (el) el.textContent = text; }", content)


def move_click(page: Page, locator, pause: int = 350) -> None:
    locator.scroll_into_view_if_needed()
    box = locator.bounding_box()
    if box:
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=14)
        page.wait_for_timeout(220)
    locator.click()
    page.wait_for_timeout(pause)


def scroll_main(page: Page, delta: int, pause: int = 1000) -> None:
    page.evaluate(
        "dy => { const el = document.querySelector('.main'); if (el) el.scrollBy({ top: dy, behavior: 'smooth' }); }",
        delta,
    )
    page.wait_for_timeout(pause)


def scroll_top(page: Page) -> None:
    page.evaluate("() => { const el = document.querySelector('.main'); if (el) el.scrollTo({ top: 0, behavior: 'smooth' }); }")
    page.wait_for_timeout(500)


def run_actions(page: Page, segment_id: str) -> None:
    if segment_id == "intro":
        hero = page.locator("h1").first
        box = hero.bounding_box()
        if box:
            page.mouse.move(box["x"] + box["width"] * 0.45, box["y"] + box["height"] / 2, steps=24)
        page.wait_for_timeout(600)
        return

    if segment_id == "search":
        query = page.locator("#queryInput")
        move_click(page, query, pause=150)
        page.keyboard.press("Control+A")
        query.fill("存算一体 SAR ADC 低功耗")
        page.wait_for_timeout(250)
        move_click(page, page.locator("#searchBtn"), pause=500)
        page.wait_for_selector(".result-card", timeout=20000)
        page.wait_for_timeout(1200)
        return

    if segment_id == "read":
        scroll_top(page)
        card = page.locator(".result-card", has_text="NeuDW-CIM").first
        if card.count() == 0:
            card = page.locator(".result-card").first
        move_click(page, card, pause=900)
        page.wait_for_selector(".paper-title", timeout=20000)
        page.wait_for_timeout(500)
        scroll_main(page, 360, pause=1200)
        return

    if segment_id == "translate":
        scroll_top(page)
        move_click(page, page.locator("button[data-read-action='translate']").first, pause=500)
        page.wait_for_selector("#modal:not([hidden])", timeout=10000)
        page.wait_for_timeout(1800)
        page.locator("#modal .modal-body").evaluate("el => el.scrollBy({ top: 120, behavior: 'smooth' })")
        page.wait_for_timeout(1200)
        move_click(page, page.locator("#modal .modal-head [data-close-modal]").first, pause=400)
        page.wait_for_selector("#modal", state="hidden", timeout=5000)
        return

    if segment_id == "compare":
        scroll_top(page)
        ids = page.locator(".result-card").evaluate_all("els => els.slice(0, 3).map(el => el.getAttribute('data-id'))")
        for paper_id in ids:
            checkbox = page.locator(f".result-check[data-id='{paper_id}']").first
            if checkbox.count():
                checkbox.check()
                page.wait_for_timeout(220)
        move_click(page, page.locator("#tabs [data-tab='compare']").first, pause=800)
        page.wait_for_selector("table.compare", timeout=20000)
        page.wait_for_timeout(900)
        scroll_main(page, 250, pause=900)
        return

    if segment_id == "innovate":
        scroll_top(page)
        move_click(page, page.locator("#tabs [data-tab='innovate']").first, pause=800)
        page.wait_for_selector(".three-col", timeout=20000)
        page.wait_for_timeout(500)
        scroll_main(page, 170, pause=800)
        return

    if segment_id == "transform":
        scroll_top(page)
        move_click(page, page.locator("#tabs [data-tab='transform']").first, pause=800)
        page.wait_for_selector(".transform-grid", timeout=20000)
        page.wait_for_timeout(500)
        scroll_main(page, 460, pause=900)
        return

    if segment_id == "gaps":
        scroll_top(page)
        move_click(page, page.locator("#tabs [data-tab='gaps']").first, pause=800)
        page.wait_for_selector(".gap-grid", timeout=20000)
        page.wait_for_timeout(500)
        scroll_main(page, 80, pause=700)
        return

    if segment_id == "ask":
        scroll_top(page)
        move_click(page, page.locator("#tabs [data-tab='ask']").first, pause=800)
        page.wait_for_selector("#askInput", timeout=20000)
        page.fill("#askInput", "五篇论文分别如何降低 ADC 开销？")
        page.wait_for_timeout(300)
        move_click(page, page.locator("#askBtn").first, pause=800)
        page.wait_for_selector(".answer-card", timeout=30000)
        page.wait_for_timeout(1200)
        scroll_main(page, 720, pause=1200)
        return

    if segment_id == "library":
        scroll_top(page)
        move_click(page, page.locator("#tabs [data-tab='library']").first, pause=800)
        page.wait_for_selector(".library-card", timeout=20000)
        page.wait_for_timeout(900)
        scroll_main(page, 210, pause=1000)
        return

    if segment_id == "upload":
        scroll_top(page)
        pdf_dir = ROOT.parent / "paperpilot" / "data" / "demo_pdfs"
        page.set_input_files(
            "#pdfInput",
            [str(pdf_dir / "2606.08947v1.pdf"), str(pdf_dir / "2609.24288v1.pdf")],
        )
        page.wait_for_function(
            "() => document.querySelector('#resultMeta').textContent.includes('已解析 2 篇')",
            timeout=60000,
        )
        page.wait_for_selector(".paper-title", timeout=30000)
        page.wait_for_timeout(700)
        scroll_main(page, 520, pause=1200)
        return

    if segment_id == "research":
        scroll_top(page)
        move_click(page, page.locator("#tabs [data-tab='topic']").first, pause=700)
        page.wait_for_selector("#topicQuery", timeout=10000)
        move_click(page, page.locator("#topicBtn").first, pause=800)
        page.wait_for_selector(".topic-stats", timeout=30000)
        page.wait_for_timeout(700)
        page.locator(".question-grid").scroll_into_view_if_needed()
        page.wait_for_timeout(900)
        move_click(page, page.locator("#tabs [data-tab='writing']").first, pause=600)
        page.wait_for_selector(".writing-head", timeout=10000)
        move_click(page, page.locator("#draftBtn").first, pause=700)
        page.wait_for_selector(".draft-card", timeout=30000)
        page.wait_for_timeout(700)
        move_click(page, page.locator("#checkSample").first, pause=300)
        move_click(page, page.locator("#checkBtn").first, pause=700)
        page.wait_for_selector(".check-item", timeout=30000)
        page.wait_for_timeout(700)
        page.locator("#checkResult").scroll_into_view_if_needed()
        page.wait_for_timeout(900)
        return

    if segment_id == "eval":
        scroll_top(page)
        move_click(page, page.locator("#tabs [data-tab='eval']").first, pause=800)
        page.wait_for_selector(".eval-targets", timeout=20000)
        page.wait_for_timeout(1200)
        summary = page.locator(".card-title", has_text="最近一次回归结果").first
        if summary.count():
            summary.scroll_into_view_if_needed()
            page.evaluate("() => { const el = document.querySelector('.main'); if (el) el.scrollBy({ top: 190, behavior: 'smooth' }); }")
            page.wait_for_timeout(900)
        return


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(
            viewport={"width": 1600, "height": 900},
            record_video_dir=str(RAW),
            record_video_size={"width": 1600, "height": 900},
        )
        page = context.new_page()
        page.on("console", lambda message: print(f"console {message.type}: {message.text}") if message.type == "error" else None)
        page.goto(BASE, wait_until="networkidle", timeout=30000)
        page.wait_for_selector(".paper-title", timeout=20000)
        page.wait_for_function("() => document.querySelectorAll('.result-card').length >= 3")
        page.evaluate(OVERLAY)
        page.mouse.move(820, 330, steps=10)
        page.wait_for_timeout(400)

        t0 = time.monotonic()
        for seg in SEGMENTS:
            wait_until(t0 + seg["start"])
            print(f"[{time.monotonic()-t0:6.2f}s] {seg['id']} start")
            set_caption(page, seg["narration"])
            try:
                run_actions(page, seg["id"])
                print(f"[{time.monotonic()-t0:6.2f}s] {seg['id']} action done")
            except Exception as exc:  # keep recording even if one UI step changes
                print(f"[{time.monotonic()-t0:6.2f}s] action {seg['id']} failed: {exc}")
            wait_until(t0 + seg["end"])
        wait_until(t0 + TIMELINE["total"] - 0.4)
        page.wait_for_timeout(400)
        video = page.video
        context.close()
        browser.close()
        raw_path = Path(video.path())
        print("raw video:", raw_path)
        convert(raw_path)


def convert(raw_path: Path) -> None:
    output = OUT / "paperpilot_demo.mp4"
    command = [
        "ffmpeg", "-y",
        "-i", str(raw_path),
        "-i", str(ROOT / "audio" / "mix.m4a"),
        "-vf", "fade=t=in:st=0:d=0.35",
        "-c:v", "libx264", "-preset", "medium", "-crf", "22",
        "-pix_fmt", "yuv420p", "-r", "25",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
        "-metadata", "title=PaperPilot 产品演示",
        str(output),
    ]
    subprocess.run(command, check=True)
    print("final:", output, output.stat().st_size)


if __name__ == "__main__":
    main()
