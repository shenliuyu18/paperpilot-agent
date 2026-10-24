from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import edge_tts

ROOT = Path(__file__).resolve().parent
VOICE = "zh-CN-YunxiNeural"
RATE = "+6%"
LEAD_IN = 1.0
GAP = 0.28


def duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


async def main() -> None:
    script = json.loads((ROOT / "demo_script.json").read_text(encoding="utf-8"))
    audio_dir = ROOT / "audio"
    audio_dir.mkdir(exist_ok=True)
    cursor = LEAD_IN
    segments = []
    for index, seg in enumerate(script["segments"], start=1):
        path = audio_dir / f"{index:02d}_{seg['id']}.mp3"
        communicate = edge_tts.Communicate(seg["narration"], VOICE, rate=RATE)
        await communicate.save(str(path))
        length = duration(path)
        segments.append(
            {
                **seg,
                "index": index,
                "audio": str(path.relative_to(ROOT)),
                "duration": round(length, 3),
                "start": round(cursor, 3),
                "end": round(cursor + length, 3),
            }
        )
        cursor += length + GAP
        print(f"{index:02d} {seg['id']:<10} {length:6.2f}s start={segments[-1]['start']:6.2f}s")
    total = round(cursor + 1.6, 3)
    timeline = {
        "title": script["title"],
        "voice": VOICE,
        "rate": RATE,
        "lead_in": LEAD_IN,
        "gap": GAP,
        "total": total,
        "segments": segments,
    }
    (ROOT / "demo_timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"TOTAL {total:.2f}s ({total/60:.2f} min)")


if __name__ == "__main__":
    asyncio.run(main())
