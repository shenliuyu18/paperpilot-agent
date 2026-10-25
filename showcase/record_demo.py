"""Record the one-click demo walkthrough used on the showcase page.

Requires Playwright (not in requirements.txt) and a local server:

    python run.py --host 127.0.0.1 --port 8768 --no-browser
    python ../showcase/record_demo.py --url http://127.0.0.1:8768/

Outputs ``demo/paperpilot-8.3.1.webm`` (raw) and, when ffmpeg is on PATH,
``showcase/paperpilot_demo_831.mp4``. The captions are injected into the page
so the recording explains itself without voice-over or post-production.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "demo"
RAW_DIR = OUT_DIR / "recordings"
RAW_NAME = "paperpilot-8.3.1.webm"
FINAL = ROOT / "showcase" / "paperpilot_demo_831.mp4"

CAPTION_JS = """
(text) => {
  let el = document.getElementById('__rec_caption');
  if (!el) {
    el = document.createElement('div');
    el.id = '__rec_caption';
    el.style.cssText = [
      'position:fixed', 'left:50%', 'bottom:34px', 'transform:translateX(-50%)',
      'z-index:99999', 'padding:12px 22px', 'border-radius:999px',
      'background:rgba(4,10,20,.92)', 'border:1px solid rgba(76,227,247,.45)',
      'color:#e9f3ff', 'font:600 17px/1.4 "Microsoft YaHei","PingFang SC",sans-serif',
      'letter-spacing:.3px', 'box-shadow:0 10px 34px rgba(0,0,0,.45)',
      'pointer-events:none', 'transition:opacity .25s ease', 'opacity:0',
      'max-width:86vw', 'text-align:center',
    ].join(';');
    document.body.appendChild(el);
  }
  el.textContent = text;
  el.style.opacity = text ? '1' : '0';
}
"""

TITLE_JS = """
([text, sub]) => {
  let el = document.getElementById('__rec_title');
  if (!el) {
    el = document.createElement('div');
    el.id = '__rec_title';
    el.style.cssText = [
      'position:fixed', 'inset:0', 'z-index:99998', 'display:flex',
      'flex-direction:column', 'align-items:center', 'justify-content:center',
      'gap:18px', 'background:radial-gradient(900px 600px at 50% 40%, #0b1a2e, #050b16 70%)',
      'transition:opacity .4s ease', 'opacity:1',
    ].join(';');
    document.body.appendChild(el);
  }
  el.innerHTML = `
    <div style="font:600 13px/1 'Cascadia Code',Consolas,monospace;letter-spacing:3px;color:#4ce3f7">PAPERPILOT 8.3.1</div>
    <div style="font:700 42px/1.25 'Microsoft YaHei','PingFang SC',sans-serif;color:#e9f3ff;text-align:center;max-width:900px">${text}</div>
    <div style="font:400 17px/1.6 'Microsoft YaHei','PingFang SC',sans-serif;color:#8fa8c8;text-align:center;max-width:820px">${sub}</div>`;
  el.style.opacity = '1';
}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8768/")
    parser.add_argument("--keep-raw", action="store_true")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for old in RAW_DIR.glob("*.webm"):
        old.unlink()

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            record_video_dir=str(RAW_DIR),
            record_video_size={"width": 1440, "height": 900},
        )
        page = context.new_page()
        page.goto(args.url, wait_until="networkidle")
        page.wait_for_timeout(600)

        page.evaluate(TITLE_JS, ["从「一个方向」到「可核查的初稿」", "证据优先 · 判断留人 · 离线可跑"])
        page.wait_for_timeout(3000)
        page.evaluate("() => { const el = document.getElementById('__rec_title'); if (el) { el.style.opacity='0'; setTimeout(()=>el.remove(), 500); } }")
        page.wait_for_timeout(700)

        page.evaluate(CAPTION_JS, "① 点「一键跑完一次完整研究」：离线、模型预算 0")
        page.wait_for_timeout(1600)
        page.click("#demoStartBtn")
        page.wait_for_selector("[data-tour-open]", timeout=120000)
        page.evaluate(CAPTION_JS, "② 15 个动作 · 21 条证据 · 16 个草稿块，项目自动打开")
        page.wait_for_timeout(4200)

        page.evaluate(CAPTION_JS, "③ 每一步都留下证据；这里只有 3 块真正绑定了证据")
        page.click("[data-tour-draft]")
        page.wait_for_timeout(3800)

        page.evaluate(CAPTION_JS, "④ 点证据旁的「原文 p.1」")
        page.locator("[data-draft-page]").first.click()
        page.wait_for_timeout(2800)
        page.evaluate(CAPTION_JS, "⑤ 这句话在 PDF 原文页里被高亮：已定位证据 · 3 处")
        page.wait_for_timeout(4600)

        page.evaluate("() => document.querySelector('[data-close-modal]')?.click()")
        page.wait_for_timeout(900)
        page.evaluate(CAPTION_JS, "没有证据就明说没有；有证据就能回到原文核对")
        page.wait_for_timeout(2600)

        page.evaluate(CAPTION_JS, "PaperPilot 8.3.1 · 一个方向 → 检索 → 证据 → 初稿 → 投稿包")
        page.wait_for_timeout(3400)

        video = page.video
        context.close()
        browser.close()
        # Playwright must still be running when the video path is resolved.
        video_path = video.path()

    raw = Path(video_path)
    target = RAW_DIR / RAW_NAME
    if raw != target:
        raw.replace(target)

    if not FINAL.parent.exists():
        FINAL.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(target),
            "-c:v", "libx264", "-preset", "slow", "-crf", "20",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an",
            str(FINAL),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        print(result.stderr[-1200:], file=sys.stderr)
        print("webm kept at", target, file=sys.stderr)
        return 1
    if not args.keep_raw:
        target.unlink(missing_ok=True)
    size = FINAL.stat().st_size
    print(f"{FINAL.relative_to(ROOT)}: {size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def _unused() -> None:  # pragma: no cover - keeps `time` import meaningful for edits
    time.sleep(0)