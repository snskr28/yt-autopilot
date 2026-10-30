"""Phase 5 acceptance: an 8-10 minute 1920x1080 video with a visual change every
3-5 s, a 1280x720 thumbnail, and chapter timestamps that match the voice.

Run: .venv\\Scripts\\python.exe scripts\\check_phase5.py jobs\\<job>
"""
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

from PIL import Image


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def seconds(stamp):
    parts = [int(p) for p in stamp.split(":")]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def main(job: Path) -> int:
    ok = True

    def report(name, passed, detail):
        nonlocal ok
        ok &= passed
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")

    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(job / "final.mp4")],
        capture_output=True, text=True, check=True).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    dur = float(info["format"]["duration"])
    report("resolution", (v["width"], v["height"]) == (1920, 1080), f"{v['width']}x{v['height']}")
    report("duration 8-10 min", 480 <= dur <= 600, f"{dur / 60:.2f} min ({dur:.1f}s)")

    shots = load(job / "visuals.json")["shots"]
    lengths = [s["end"] - s["start"] for s in shots]
    in_range = sum(1 for d in lengths if 3.0 <= d <= 5.0)
    kinds = {k: sum(1 for s in shots if s["kind"] == k) for k in ("video", "photo", "card", "motion")}
    print(f"       shots: {len(shots)} ({kinds}), length min {min(lengths):.1f}s / "
          f"median {statistics.median(lengths):.1f}s / max {max(lengths):.1f}s; "
          f"{in_range / len(lengths):.0%} within 3-5 s")
    report("visual change every 3-5 s", max(lengths) <= 6.0 and 3.0 <= statistics.median(lengths) <= 5.0
           and in_range / len(lengths) >= 0.8,
           "max gap <= 6 s, median 3-5 s, >= 80% of shots in 3-5 s")

    thumb = next((p for p in (job / "thumb.png", job / "thumb.jpg") if p.exists()), None)
    if thumb:
        size = Image.open(thumb).size
        report("thumbnail", size == (1280, 720) and thumb.stat().st_size <= 2 * 1024 * 1024,
               f"{thumb.name} {size[0]}x{size[1]}, {thumb.stat().st_size // 1024} KB")
    else:
        report("thumbnail", False, "missing")

    script, voice = load(job / "script.json"), load(job / "voice.json")
    lines = re.findall(r"^\s*((?:\d{1,2}:)?\d{1,2}:\d{2})\s+(.+)$", script["description"], re.M)
    stamps = [seconds(t) for t, _ in lines]
    real = []
    for i, scene in enumerate(script["scenes"]):
        if not real or scene["section"] != script["scenes"][i - 1]["section"]:
            real.append(0.0 if not real else voice["scenes"][i]["start"])
    print("       chapters: " + "; ".join(f"{t} {h}" for t, h in lines))
    gaps = [b - a for a, b in zip(stamps, stamps[1:] + [dur])]
    report("chapters", len(stamps) >= 3 and stamps[0] == 0 and min(gaps) >= 10
           and len(stamps) == len(real) and all(abs(s - r) < 1.0 for s, r in zip(stamps, real)),
           f"{len(stamps)} chapters, first at 0:00, shortest {min(gaps):.0f}s, "
           f"all within 1 s of the voice section starts")

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
