"""Thumbnail for long videos: 1280x720 PNG, under YouTube's 2 MB limit.

Picks the most striking frame from the video's stock shots (colourfulness +
contrast), darkens it, and writes script.json's thumbnail_text (3-5 words) in a
heavy font. Layouts rotate (least recently used) for the variety rule.
"""
import argparse
import subprocess
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance

from .assemble import HIGHLIGHT, _wrap, font, least_recent
from .common import get_logger, read_json, write_json

log = get_logger()

W, H = 1280, 720
LAYOUTS = ["left_block", "center_band", "bottom_bar"]
CANDIDATES = 14
MAX_BYTES = 2 * 1024 * 1024


def frame_at(video: Path, t: float) -> Image.Image:
    png = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video),
         "-frames:v", "1", "-vf", f"scale={W}:{H}", "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True, check=True).stdout
    return Image.open(BytesIO(png)).convert("RGB")


def striking(img: Image.Image) -> float:
    """Hasler-Suesstrunk colourfulness plus luminance contrast."""
    a = np.asarray(img.resize((320, 180)), dtype=np.float32)
    rg = a[..., 0] - a[..., 1]
    yb = 0.5 * (a[..., 0] + a[..., 1]) - a[..., 2]
    colour = np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean())
    return float(colour + 0.5 * a.mean(axis=2).std())


def pick_frame(job_dir: Path) -> Image.Image:
    shots = [s for s in read_json(job_dir / "visuals.json")["shots"] if s["kind"] in ("video", "photo")]
    # favour the first 60% of the video, where the most relevant visuals usually are
    early = [s for s in shots if s["start"] < shots[-1]["end"] * 0.6] or shots
    step = max(1, len(early) // CANDIDATES)
    frames = [frame_at(job_dir / "final.mp4", (s["start"] + s["end"]) / 2) for s in early[::step][:CANDIDATES]]
    return max(frames, key=striking)


def compose(bg: Image.Image, text: str, layout: str) -> Image.Image:
    img = ImageEnhance.Contrast(bg).enhance(1.15)
    img = ImageEnhance.Brightness(img).enhance(0.62)
    draw = ImageDraw.Draw(img, "RGBA")
    words = text.upper().split()

    if layout == "left_block":
        draw.rectangle([0, 0, int(W * 0.58), H], fill=(0, 0, 0, 120))
        fnt = font("anton", 132)
        lines = [[words[i] for i in line] for line in _wrap(words, fnt, int(W * 0.50))]
        if len(lines) == 1 and len(words) > 2:
            lines = [words[:len(words) // 2], words[len(words) // 2:]]
        y = (H - len(lines) * 150) / 2
        for row, line in enumerate(lines):
            colour = HIGHLIGHT if row == len(lines) - 1 else (255, 255, 255)
            draw.text((60, y + row * 150), " ".join(line), font=fnt, fill=colour,
                      stroke_width=6, stroke_fill=(0, 0, 0))
    elif layout == "center_band":
        fnt = font("anton", 120)
        lines = [" ".join(words[i] for i in line) for line in _wrap(words, fnt, int(W * 0.86))]
        block = len(lines) * 138
        top = (H - block) / 2
        draw.rectangle([0, top - 30, W, top + block + 20], fill=(0, 0, 0, 150))
        for row, line in enumerate(lines):
            draw.text(((W - fnt.getlength(line)) / 2, top + row * 138), line, font=fnt,
                      fill=(255, 255, 255), stroke_width=5, stroke_fill=(0, 0, 0))
    else:  # bottom_bar
        fnt = font("bebas", 150)
        line = " ".join(words)
        while fnt.getlength(line) > W * 0.9 and fnt.size > 80:
            fnt = font("bebas", fnt.size - 10)
        bar_h = fnt.size + 50
        draw.rectangle([0, H - bar_h, W, H], fill=HIGHLIGHT + (255,))
        draw.text(((W - fnt.getlength(line)) / 2, H - bar_h + 18), line, font=fnt, fill=(15, 15, 15))
    return img


def run(job_dir: Path) -> Path:
    status = read_json(job_dir / "status.json")
    script = read_json(job_dir / "script.json")
    layout = status.get("thumb_layout") or least_recent(LAYOUTS, "thumb_layout", status["job_id"])
    img = compose(pick_frame(job_dir), script["thumbnail_text"], layout)
    out = job_dir / "thumb.png"
    img.save(out, optimize=True)
    if out.stat().st_size > MAX_BYTES:
        out = out.with_suffix(".jpg")
        img.save(out, quality=90)
        (job_dir / "thumb.png").unlink()
    status["thumb_layout"] = layout
    write_json(job_dir / "status.json", status)
    log.info("Thumbnail %s (%s, %d KB)", out.name, layout, out.stat().st_size // 1024)
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Make the thumbnail for one long-video job")
    parser.add_argument("job_dir", type=Path)
    run(parser.parse_args().job_dir)
