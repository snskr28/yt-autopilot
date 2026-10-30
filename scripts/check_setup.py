"""Phase 0 acceptance check: claude -p, ffmpeg and Kokoro all work.

Run: .venv\\Scripts\\python.exe scripts\\check_setup.py
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
results = []


def check(name, fn):
    try:
        detail = fn()
        results.append((name, True, detail))
    except Exception as e:  # report and keep going so every check runs
        results.append((name, False, f"{type(e).__name__}: {e}"))


def no_api_key():
    if os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is set; unset it so claude -p uses the Pro login")
    return "not set (good)"


def claude_hi():
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude not on PATH")
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    out = subprocess.run(
        [exe, "-p", "say hi", "--output-format", "json"],
        capture_output=True, text=True, encoding="utf-8", timeout=180, env=env,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or out.stdout.strip())
    data = json.loads(out.stdout)
    if data.get("is_error"):
        raise RuntimeError(data.get("result"))
    return f"reply: {data.get('result', '').strip()[:80]!r}"


def ffmpeg_version():
    out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=True)
    return out.stdout.splitlines()[0]


def kokoro_line():
    import numpy as np
    import soundfile as sf
    from kokoro import KPipeline

    pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    chunks = [audio for _, _, audio in pipeline(
        "Welcome to the channel. Today, a strange fact about the Moon.", voice="af_heart")]
    audio = np.concatenate([np.asarray(c) for c in chunks])
    out = ROOT / "logs" / "kokoro_test.wav"
    out.parent.mkdir(exist_ok=True)
    sf.write(out, audio, 24000)
    return f"{len(audio) / 24000:.1f}s of audio -> {out.relative_to(ROOT)}"


check("ANTHROPIC_API_KEY", no_api_key)
check("claude -p \"say hi\"", claude_hi)
check("ffmpeg -version", ffmpeg_version)
check("Kokoro one-liner", kokoro_line)

for name, ok, detail in results:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
sys.exit(0 if all(ok for _, ok, _ in results) else 1)
