from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
SR = 44100


def decode(path: Path) -> np.ndarray:
    wav_path = path.with_suffix(".wav")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(path), "-ac", "1", "-ar", str(SR), str(wav_path)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    with wave.open(str(wav_path), "rb") as handle:
        data = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
    wav_path.unlink(missing_ok=True)
    return data.astype(np.float64) / 32768.0


def pad(total: float) -> np.ndarray:
    n = int(total * SR)
    t = np.arange(n) / SR
    left = np.zeros(n)
    right = np.zeros(n)
    chords = [
        (110.0, [1.0, 1.1892, 1.4983, 2.0]),   # A minor
        (87.31, [1.0, 1.2599, 1.4983, 2.0]),    # F
        (130.81, [1.0, 1.2599, 1.4983, 2.0]),   # C
        (98.0, [1.0, 1.2599, 1.4983, 2.0]),     # G
    ]
    for i, (root, ratios) in enumerate(chords):
        start = i * 8.0
        while start < total:
            end = min(total, start + 9.0)
            local_n = int((end - start) * SR)
            local_t = np.arange(local_n) / SR
            env = np.minimum(1.0, local_t / 1.2) * np.minimum(1.0, (end - start - local_t) / 1.2)
            env = np.clip(env, 0, 1)
            for j, ratio in enumerate(ratios):
                freq = root * ratio
                wave_l = np.sin(2 * np.pi * freq * (1 + j * 0.0004) * local_t + j)
                wave_r = np.sin(2 * np.pi * freq * (1 - j * 0.0004) * local_t + j + 0.4)
                pan = 0.5 + (j - 1.5) * 0.15
                left[int(start * SR):int(start * SR) + local_n] += wave_l * env * (1 - pan) * 0.45
                right[int(start * SR):int(start * SR) + local_n] += wave_r * env * pan * 0.45
            start += 8.0
    # gentle low pulse
    for beat in np.arange(2.0, total, 2.0):
        start = int(beat * SR)
        length = min(int(0.35 * SR), n - start)
        if length <= 0:
            continue
        local = np.arange(length) / SR
        env = np.exp(-local * 10) * 0.035
        pulse = np.sin(2 * np.pi * 55 * local) * env
        left[start:start + length] += pulse
        right[start:start + length] += pulse
    fade = int(1.5 * SR)
    left[:fade] *= np.linspace(0, 1, fade)
    right[:fade] *= np.linspace(0, 1, fade)
    left[-fade:] *= np.linspace(1, 0, fade)
    right[-fade:] *= np.linspace(1, 0, fade)
    peak = max(np.max(np.abs(left)), np.max(np.abs(right)), 1e-9)
    return np.stack([left / peak * 0.08, right / peak * 0.08], axis=1)


def write_wav(path: Path, stereo: np.ndarray) -> None:
    pcm = (np.clip(stereo, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(pcm.tobytes())


def main() -> None:
    timeline = json.loads((ROOT / "demo_timeline.json").read_text(encoding="utf-8"))
    total = timeline["total"] + 1.0
    narration = np.zeros((int(total * SR), 2))
    for seg in timeline["segments"]:
        audio = decode(ROOT / seg["audio"])
        start = int(seg["start"] * SR)
        narration[start:start + len(audio), 0] += audio
        narration[start:start + len(audio), 1] += audio
    music = pad(total)
    n = min(len(narration), len(music))
    mix = narration[:n] + music[:n] * 0.7
    mix = np.tanh(mix * 1.02)
    peak = max(np.max(np.abs(mix)), 1e-9)
    mix = mix / peak * 0.92
    wav_path = ROOT / "audio" / "mix.wav"
    write_wav(wav_path, mix)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path), "-c:a", "aac", "-b:a", "192k", str(ROOT / "audio" / "mix.m4a")],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    wav_path.unlink(missing_ok=True)
    print(f"mix -> audio/mix.m4a  {n/SR:.2f}s")


if __name__ == "__main__":
    main()
