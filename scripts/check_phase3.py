"""Phase 3 acceptance: final.mp4 is 1080x1920, 30-60 s, no black frames,
music audible under the voice. Also writes a phone-size contact sheet
(preview.png) to judge caption readability by eye.

Run: .venv\\Scripts\\python.exe scripts\\check_phase3.py jobs\\<job>
"""
import json
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def mean_volume(path, start, dur):
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
                          "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float(re.search(r"mean_volume: (-?[\d.]+) dB", err).group(1))


def main(job: Path) -> int:
    final = job / "final.mp4"
    info = probe(final)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    dur = float(info["format"]["duration"])
    fps = eval(v["r_frame_rate"])
    ok = True

    def report(name, passed, detail):
        nonlocal ok
        ok &= passed
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")

    report("resolution", (v["width"], v["height"]) == (1080, 1920), f"{v['width']}x{v['height']}")
    report("duration", 30 <= dur <= 60, f"{dur:.2f}s")
    report("codecs/fps", v["codec_name"] == "h264" and a["codec_name"] == "aac" and round(fps) == 30,
           f"{v['codec_name']} {fps:.2f} fps, {a['codec_name']}")

    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(final), "-vf",
                          "blackdetect=d=0.1:pix_th=0.10", "-an", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    blacks = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)
    report("no black frames", not blacks, f"{len(blacks)} black segment(s) {blacks[:3]}")

    # Music under the voice: in the pauses between scenes only music plays.
    voice = json.loads((job / "voice.json").read_text(encoding="utf-8"))
    scenes = voice["scenes"]
    gaps = [(scenes[i]["end"] + 0.05, scenes[i + 1]["start"] - 0.05) for i in range(len(scenes) - 1)]
    gap_db = [mean_volume(final, g0, g1 - g0) for g0, g1 in gaps if g1 - g0 > 0.1]
    speech_db = [mean_volume(final, s["start"], s["end"] - s["start"]) for s in scenes]
    music_level = sum(gap_db) / len(gap_db)
    speech_level = sum(speech_db) / len(speech_db)
    under = speech_level - music_level
    report("music present in pauses", music_level > -60, f"{music_level:.1f} dB mean between scenes")
    report("music under voice", 14 <= under <= 28, f"speech {speech_level:.1f} dB vs music {music_level:.1f} dB "
                                                   f"({under:.1f} dB under; target about 20)")

    # Contact sheet at phone size (each frame 270x480, a typical phone thumbnail of the real 9:16 view)
    times = [dur * k / 9 + 1 for k in range(8)]
    frames = []
    for t in times:
        png = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(final),
                              "-frames:v", "1", "-vf", "scale=360:640", "-f", "image2pipe", "-vcodec", "png", "-"],
                             capture_output=True, check=True).stdout
        from io import BytesIO
        frames.append(Image.open(BytesIO(png)))
    sheet = Image.new("RGB", (360 * 4, 640 * 2), "black")
    for i, f in enumerate(frames):
        sheet.paste(f, ((i % 4) * 360, (i // 4) * 640))
    sheet.save(job / "preview.png")
    print(f"contact sheet: {job / 'preview.png'} (frames shown at 360x640, roughly phone size)")

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
