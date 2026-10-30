"""Phase 2 acceptance: voice.wav is complete and captions.json timings match the audio.

Run: .venv\\Scripts\\python.exe scripts\\check_phase2.py jobs\\<job>

Timing check is independent of Whisper: it finds where speech actually starts
and stops in each scene from the waveform energy, and compares that with the
first/last caption word of the scene. Tolerance 0.2 s.
"""
import difflib
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

TOLERANCE = 0.2
FRAME = 0.01  # 10 ms energy frames


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def norm_words(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def main(job: Path) -> int:
    script, voice, caps = load(job / "script.json"), load(job / "voice.json"), load(job / "captions.json")
    audio, sr = sf.read(job / "voice.wav")
    hop = int(FRAME * sr)
    rms = np.sqrt(np.mean(audio[: len(audio) // hop * hop].reshape(-1, hop) ** 2, axis=1))
    db = 20 * np.log10(rms + 1e-9)
    active = db > db.max() - 40  # within 40 dB of the loudest frame counts as speech

    ok = True
    print(f"voice.wav: {len(audio) / sr:.2f}s, {sr} Hz; voice.json duration {voice['duration']:.2f}s")
    print(f"{'scene':>5} {'speech on':>9} {'1st word':>9} {'diff':>6} | {'speech off':>10} {'last word':>9} {'diff':>6}")
    worst = 0.0
    for scene in voice["scenes"]:
        a, b = scene["start"], scene["end"]
        frames = np.nonzero(active[int(a / FRAME): int(b / FRAME) + 1])[0]
        on, off = a + frames[0] * FRAME, a + (frames[-1] + 1) * FRAME
        words = [w for w in caps["words"] if a - 0.1 <= (w["start"] + w["end"]) / 2 <= b + 0.1]
        if not words:
            print(f"{scene['index']:>5} no caption words in this scene")
            ok = False
            continue
        d1, d2 = words[0]["start"] - on, words[-1]["end"] - off
        worst = max(worst, abs(d1), abs(d2))
        flag = "" if abs(d1) <= TOLERANCE and abs(d2) <= TOLERANCE else "  <-- off"
        ok &= not flag
        print(f"{scene['index']:>5} {on:9.2f} {words[0]['start']:9.2f} {d1:+6.2f} | "
              f"{off:10.2f} {words[-1]['end']:9.2f} {d2:+6.2f}{flag}")
    print(f"worst boundary difference: {worst:.2f}s (tolerance {TOLERANCE}s)")

    said = norm_words(" ".join(w["word"] for w in caps["words"]))
    wrote = norm_words(" ".join(s["narration"] for s in script["scenes"]))
    match = difflib.SequenceMatcher(None, wrote, said).ratio()
    print(f"transcript vs script word match: {match:.1%} ({len(said)} heard / {len(wrote)} written)")
    ok &= match >= 0.95

    sizes = [len(c["words"]) for c in caps["chunks"]]
    print(f"caption chunks: {len(sizes)}, words per chunk {min(sizes)}-{max(sizes)}")
    ok &= max(sizes) <= 4

    meas = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(job / "voice.wav"),
                           "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
    lufs = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", meas)[-1])
    print(f"integrated loudness: {lufs:.1f} LUFS (target -16)")
    ok &= abs(lufs + 16) <= 1.0

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
