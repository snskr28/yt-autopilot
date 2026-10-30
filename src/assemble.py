"""Assemble final.mp4 with MoviePy 2.x.

Timeline: stock shots (videos cover-cropped, photos with a slow Ken Burns
zoom), on-screen text cards at the top, animated captions in the job's
caption style, voice plus a music bed about 20 dB under it, fade out.
Export: H.264 + AAC, 30 fps, preset veryfast, CRF 20.
"""
import argparse
import os
import random
import subprocess
from pathlib import Path

import numpy as np
from moviepy import (AudioFileClip, CompositeAudioClip, CompositeVideoClip, ImageClip,
                     VideoClip, VideoFileClip, concatenate_videoclips, vfx)
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .common import ASSETS_DIR, CONFIG, get_logger, read_json, write_json
from .jobs import history, list_jobs

log = get_logger()

FPS = 30
VOICE_LUFS = -16
MUSIC_UNDER_DB = 20
FADE_OUT = 0.4
FONTS = ASSETS_DIR / "fonts"
MUSIC_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
HIGHLIGHT = (255, 214, 10)


class AssembleError(RuntimeError):
    pass


# ---------- variety: caption style and music rotate, least recently used first ----------

def _recent_values(key: str, exclude_job: str) -> list[str]:
    """Values of `key` across published videos and other produced jobs, oldest first."""
    seen = [h.get(key) for h in history()]
    seen += [j.get(key) for j in sorted(list_jobs(), key=lambda j: j.get("produced_at") or "")
             if j["job_id"] != exclude_job and j.get("produced_at")]
    return [v for v in seen if v]


def least_recent(options: list[str], key: str, exclude_job: str) -> str:
    recent = _recent_values(key, exclude_job)
    last_use = {o: max((i for i, v in enumerate(recent) if v == o), default=-1) for o in options}
    oldest = min(last_use.values())
    return random.choice([o for o in options if last_use[o] == oldest])


# ---------- text rendering ----------

def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    if name == "montserrat":
        f = ImageFont.truetype(str(FONTS / "Montserrat-Variable.ttf"), size)
        f.set_variation_by_axes([850])  # ExtraBold/Black weight
        return f
    files = {"anton": "Anton-Regular.ttf", "bebas": "BebasNeue-Regular.ttf"}
    return ImageFont.truetype(str(FONTS / files[name]), size)


def _wrap(words: list[str], fnt, max_width: int) -> list[list[int]]:
    """Split word indices into at most two lines that fit max_width."""
    space = fnt.getlength(" ")
    widths = [fnt.getlength(w) for w in words]
    if sum(widths) + space * (len(words) - 1) <= max_width or len(words) == 1:
        return [list(range(len(words)))]
    best = min(range(1, len(words)), key=lambda k: abs(sum(widths[:k]) - sum(widths[k:])))
    return [list(range(best)), list(range(best, len(words)))]


STYLES = {
    # font, size as fraction of frame width, uppercase, fill, stroke, box, y (fraction of height)
    "bold_center": dict(font="anton", size=0.105, upper=True, fill=(255, 255, 255),
                        stroke=10, box=None, y=0.60, karaoke=False),
    "karaoke_lower": dict(font="montserrat", size=0.088, upper=False, fill=(255, 255, 255),
                          stroke=8, box=None, y=0.66, karaoke=True),
    "boxed": dict(font="bebas", size=0.11, upper=True, fill=(20, 20, 20),
                  stroke=0, box=(255, 255, 255, 235), y=0.62, karaoke=False),
}


# Text sizes are fractions of frame width, tuned for 9:16; 16:9 scales them down
# and moves captions to the lower third.
LAYOUT = {
    "short": dict(scale=1.0, caption_y=None, band_y=0.13, max_line=0.84),
    "long": dict(scale=0.55, caption_y=0.86, band_y=0.06, max_line=0.70),
}


def render_caption(words: list[str], style: dict, frame_w: int, active: int | None = None,
                   scale: float = 1.0, max_line: float = 0.84) -> np.ndarray:
    fnt = font(style["font"], int(style["size"] * frame_w * scale))
    stroke = max(2, int(style["stroke"] * scale)) if style["stroke"] else 0
    style = {**style, "stroke": stroke}
    words = [w.upper() if style["upper"] else w for w in words]
    lines = _wrap(words, fnt, int(frame_w * max_line))
    space = fnt.getlength(" ")
    ascent, descent = fnt.getmetrics()
    line_h = int((ascent + descent) * 1.05)
    pad = 28 + style["stroke"]
    widths = [sum(fnt.getlength(words[i]) for i in line) + space * (len(line) - 1) for line in lines]
    img = Image.new("RGBA", (frame_w, line_h * len(lines) + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for row, (line, width) in enumerate(zip(lines, widths)):
        x = (frame_w - width) / 2
        y = pad + row * line_h
        if style["box"]:
            draw.rounded_rectangle([x - 24, y - 6, x + width + 24, y + line_h + 2], 18, fill=style["box"])
        for i in line:
            fill = HIGHLIGHT if style["karaoke"] and i == active else style["fill"]
            draw.text((x, y), words[i], font=fnt, fill=fill,
                      stroke_width=style["stroke"], stroke_fill=(0, 0, 0))
            x += fnt.getlength(words[i]) + space
    return np.array(img)


CARD_COLORS = {  # full-screen card background per caption style, for variety
    "bold_center": ((14, 22, 48), (40, 18, 70)),
    "karaoke_lower": ((10, 38, 40), (12, 70, 60)),
    "boxed": ((48, 18, 14), (90, 40, 10)),
}


def render_fullcard(text: str, w: int, h: int, style_name: str) -> np.ndarray:
    """Full-screen key-fact card (long videos): big text on a diagonal gradient."""
    c1, c2 = CARD_COLORS.get(style_name, CARD_COLORS["bold_center"])
    yy, xx = np.mgrid[0:h, 0:w]
    t = ((xx / w + yy / h) / 2)[..., None]
    img = Image.fromarray((np.array(c1) * (1 - t) + np.array(c2) * t).astype(np.uint8))
    draw = ImageDraw.Draw(img)
    fnt = font("anton", int(min(w, h) * 0.13))
    words = text.upper().split()
    lines = [" ".join(words[i] for i in line) for line in _wrap(words, fnt, int(w * 0.80))]
    ascent, descent = fnt.getmetrics()
    line_h = int((ascent + descent) * 1.08)
    top = (h - line_h * len(lines)) / 2
    for row, line in enumerate(lines):
        draw.text(((w - fnt.getlength(line)) / 2, top + row * line_h), line, font=fnt, fill=(255, 255, 255))
    bar_w = int(w * 0.08)
    draw.rectangle([(w - bar_w) / 2, top - 40, (w + bar_w) / 2, top - 30], fill=HIGHLIGHT)
    return np.array(img)


MOTION_PALETTES = [((18, 32, 70), (70, 30, 90)), ((10, 50, 55), (20, 90, 70)),
                   ((60, 24, 18), (110, 60, 20)), ((24, 24, 30), (60, 60, 80))]


def motion_shot(text: str, w: int, h: int, duration: float, variant: int) -> VideoClip:
    """Generated filler when no approved footage fits: a slowly drifting gradient
    with the scene's key phrase. variant changes palette and drift direction."""
    c1, c2 = (np.array(c, dtype=np.float32) for c in MOTION_PALETTES[variant % len(MOTION_PALETTES)])
    big_w, big_h = int(w * 1.4), int(h * 1.4)
    yy, xx = np.mgrid[0:big_h, 0:big_w].astype(np.float32)
    t = (np.sin(xx / big_w * 3.1 + yy / big_h * 2.3) + 1) / 2
    bg = (c1 * (1 - t[..., None]) + c2 * t[..., None]).astype(np.uint8)
    overlay = None
    if text:
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        fnt = font("montserrat", int(min(w, h) * 0.075))
        words = text.split()
        lines = [" ".join(words[i] for i in line) for line in _wrap(words, fnt, int(w * 0.75))]
        line_h = int(sum(fnt.getmetrics()) * 1.15)
        top = (h - line_h * len(lines)) / 2
        for row, line in enumerate(lines):
            draw.text(((w - fnt.getlength(line)) / 2, top + row * line_h), line, font=fnt,
                      fill=(255, 255, 255, 235))
        overlay = np.asarray(img, dtype=np.float32)
        alpha = overlay[..., 3:4] / 255.0
    dx, dy = (1, 1) if variant % 2 == 0 else (-1, 1)

    def frame(tt):
        p = tt / duration
        x0 = int((big_w - w) * (0.5 + dx * (p - 0.5) * 0.8))
        y0 = int((big_h - h) * (0.5 + dy * (p - 0.5) * 0.8))
        crop = bg[y0:y0 + h, x0:x0 + w].astype(np.float32)
        if overlay is not None:
            crop = crop * (1 - alpha) + overlay[..., :3] * alpha
        return crop.astype(np.uint8)

    return VideoClip(frame_function=frame, duration=duration)


def render_card(text: str, frame_w: int, scale: float = 1.0) -> np.ndarray:
    """On-screen text: white on a dark translucent band."""
    fnt = font("montserrat", int(frame_w * 0.055 * scale))
    words = text.split()
    lines = [" ".join(words[i] for i in line) for line in _wrap(words, fnt, int(frame_w * 0.80))]
    ascent, descent = fnt.getmetrics()
    line_h = int((ascent + descent) * 1.1)
    width = max(fnt.getlength(l) for l in lines)
    img = Image.new("RGBA", (frame_w, line_h * len(lines) + 60), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    x0 = (frame_w - width) / 2
    draw.rounded_rectangle([x0 - 32, 10, x0 + width + 32, 30 + line_h * len(lines) + 10], 22,
                           fill=(0, 0, 0, 170))
    for row, line in enumerate(lines):
        draw.text(((frame_w - fnt.getlength(line)) / 2, 25 + row * line_h), line, font=fnt,
                  fill=(255, 255, 255))
    return np.array(img)


# ---------- visuals ----------

ASPECT_TOLERANCE = 1.35  # beyond this mismatch, fit over a blurred fill instead of cropping
FG_SHARE = 0.55  # mismatched footage spans at least this share of the frame's long side


def _center_box(img: Image.Image, win_w: float, win_h: float) -> tuple[float, float, float, float]:
    """A centered crop window, clamped to the image (float rounding can push it past an edge)."""
    win_w, win_h = min(win_w, img.width), min(win_h, img.height)
    left, top = (img.width - win_w) / 2, (img.height - win_h) / 2
    return max(0.0, left), max(0.0, top), min(img.width, left + win_w), min(img.height, top + win_h)


def frame_image(img: Image.Image, w: int, h: int, zoom: float) -> np.ndarray:
    """Place a source frame into w x h.

    Similar aspect: cover-crop. Very different aspect (landscape footage in a
    9:16 Short, or a portrait photo in a 16:9 video): the picture fills the
    frame's short side, spans at least FG_SHARE of its long side, and sits over
    a blurred, darkened copy of itself, so most of it stays visible.
    """
    src_aspect, dst_aspect = img.width / img.height, w / h
    mismatch = max(src_aspect, dst_aspect) / min(src_aspect, dst_aspect)
    if mismatch <= ASPECT_TOLERANCE:
        scale = max(w / img.width, h / img.height) * zoom
        return np.array(img.resize((w, h), Image.BILINEAR, box=_center_box(img, w / scale, h / scale)))
    # background: cover-crop at 1/8 size, blur, scale back up, darken
    cover = max(w / img.width, h / img.height)
    small = img.resize((w // 8, h // 8), Image.BILINEAR, box=_center_box(img, w / cover, h / cover))
    bg = small.filter(ImageFilter.GaussianBlur(3)).resize((w, h), Image.BILINEAR)
    bg = Image.eval(bg, lambda v: int(v * 0.45))
    if dst_aspect < 1:  # portrait frame: fill the width, span >= FG_SHARE of the height
        scale = max(w / img.width, FG_SHARE * h / img.height) * zoom
    else:               # landscape frame: fill the height, span >= FG_SHARE of the width
        scale = max(h / img.height, FG_SHARE * w / img.width) * zoom
    fw, fh = max(1, int(img.width * scale)), max(1, int(img.height * scale))
    fg = img.resize((fw, fh), Image.BILINEAR)
    if fw > w or fh > h:
        fg = fg.crop(((fw - min(fw, w)) // 2, (fh - min(fh, h)) // 2,
                      (fw - min(fw, w)) // 2 + min(fw, w), (fh - min(fh, h)) // 2 + min(fh, h)))
    bg.paste(fg, ((w - fg.width) // 2, (h - fg.height) // 2))
    return np.array(bg)


def video_shot(src, w: int, h: int, duration: float) -> VideoClip:
    def frame(t):
        return frame_image(Image.fromarray(src.get_frame(min(t, src.duration - 0.05))), w, h, 1.0)
    return VideoClip(frame_function=frame, duration=duration)


def photo_shot(path: Path, w: int, h: int, duration: float, zoom_in: bool) -> VideoClip:
    """Slow Ken Burns zoom (in or out by ~12% over the shot)."""
    img = Image.open(path).convert("RGB")
    longest = 2 * max(w, h)  # keep per-frame resizing cheap
    if max(img.size) > longest:
        img.thumbnail((longest, longest), Image.LANCZOS)

    def frame(t):
        p = t / duration
        return frame_image(img, w, h, 1.0 + 0.12 * p if zoom_in else 1.12 - 0.12 * p)
    return VideoClip(frame_function=frame, duration=duration)


def build_shots(job_dir: Path, shots: list[dict], w: int, h: int, style_name: str) -> tuple[list, list]:
    clips, readers = [], []
    for n, shot in enumerate(shots):
        d = shot["end"] - shot["start"]
        if shot["kind"] == "card":
            clips.append(ImageClip(render_fullcard(shot["text"], w, h, style_name)).with_duration(d))
            continue
        if shot["kind"] == "motion":
            clips.append(motion_shot(shot.get("text", ""), w, h, d, shot.get("variant", n)))
            continue
        path = job_dir / shot["file"]
        if shot["kind"] == "video":
            src = VideoFileClip(str(path), audio=False)
            readers.append(src)
            clip = video_shot(src, w, h, d)
        else:
            clip = photo_shot(path, w, h, d, zoom_in=n % 2 == 0)
        clips.append(clip.with_duration(d))
    return clips, readers


# ---------- audio ----------

def music_bed(track: Path, duration: float, out: Path) -> Path:
    """Loop/trim the track to length, set it ~20 dB under the voice, fade both ends."""
    level = VOICE_LUFS - MUSIC_UNDER_DB
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-stream_loop", "-1", "-i", str(track),
         "-t", f"{duration:.3f}", "-af",
         f"loudnorm=I={level}:TP=-2:LRA=11,afade=t=in:d=1,afade=t=out:st={duration - 1.5:.3f}:d=1.5",
         "-ar", "44100", "-ac", "2", str(out)],
        check=True,
    )
    return out


# ---------- main ----------

def run(job_dir: Path, preview_without_music: bool = False) -> Path:
    status = read_json(job_dir / "status.json")
    script = read_json(job_dir / "script.json")
    voice = read_json(job_dir / "voice.json")
    visuals = read_json(job_dir / "visuals.json")
    captions = read_json(job_dir / "captions.json")
    video_type = status["type"]
    w, h = CONFIG["formats"][video_type]["resolution"]
    total = visuals["duration"]
    if video_type == "short" and not 30 <= total <= 60:
        raise AssembleError(f"Short would be {total:.1f}s; must be 30-60s. Shorten the script.")

    tracks = sorted(p for p in (ASSETS_DIR / "music").iterdir() if p.suffix.lower() in MUSIC_EXT)
    if not tracks and not preview_without_music:
        raise AssembleError("No music in assets/music/. Add tracks from the YouTube Audio Library.")
    style_name = status.get("caption_style") or least_recent(
        CONFIG["variety"]["caption_styles"], "caption_style", status["job_id"])
    track_name = None if preview_without_music else (
        status.get("music") or least_recent([t.name for t in tracks], "music", status["job_id"]))
    style = STYLES[style_name]
    log.info("Assembling %s: caption style %s, music %s", job_dir.name, style_name, track_name)

    if video_type == "long" and not 480 <= total <= 600:
        log.warning("Long video is %.1f min; target is 8-10", total / 60)
    layout = LAYOUT[video_type]
    shot_clips, readers = build_shots(job_dir, visuals["shots"], w, h, style_name)
    layers = [concatenate_videoclips(shot_clips, method="chain")]

    # on-screen text band, one per scene, near the top (skipped where a full-screen card showed it)
    carded = {s["scene"] for s in visuals["shots"] if s["kind"] == "card"}
    starts = [s["start"] for s in voice["scenes"]]
    for i, scene in enumerate(script["scenes"]):
        # long videos show key facts only as full-screen cards; a band on most scenes is clutter
        if scene.get("on_screen_text") and i not in carded and video_type == "short":
            a = 0.0 if i == 0 else starts[i]
            b = starts[i + 1] if i + 1 < len(starts) else voice["duration"]
            layers.append(ImageClip(render_card(scene["on_screen_text"], w, layout["scale"]))
                          .with_start(a).with_duration(b - a)
                          .with_position(("center", int(h * layout["band_y"]))))

    # captions: each chunk stays up until the next one (or briefly after its last word)
    caption_y = layout["caption_y"] or style["y"]
    text_args = dict(scale=layout["scale"], max_line=layout["max_line"])
    chunks = captions["chunks"]
    for i, chunk in enumerate(chunks):
        end = chunks[i + 1]["start"] if i + 1 < len(chunks) else chunk["end"] + 0.3
        end = min(end, chunk["end"] + 0.6)
        words = [wd["word"] for wd in chunk["words"]]
        if style["karaoke"]:
            for k, wd in enumerate(chunk["words"]):
                a = chunk["start"] if k == 0 else wd["start"]
                b = chunk["words"][k + 1]["start"] if k + 1 < len(words) else end
                img = render_caption(words, style, w, active=k, **text_args)
                layers.append(ImageClip(img).with_start(a).with_duration(max(b - a, 0.04))
                              .with_position(("center", int(h * caption_y - img.shape[0] / 2))))
        else:
            img = render_caption(words, style, w, **text_args)
            layers.append(ImageClip(img).with_start(chunk["start"]).with_duration(end - chunk["start"])
                          .with_position(("center", int(h * caption_y - img.shape[0] / 2))))

    video = CompositeVideoClip(layers, size=(w, h)).with_duration(total)
    video = video.with_effects([vfx.FadeOut(FADE_OUT)])
    voice_clip = AudioFileClip(str(job_dir / "voice.wav"))
    audio_clips = [voice_clip]
    if track_name:
        bed = music_bed(ASSETS_DIR / "music" / track_name, total, job_dir / "music_bed.wav")
        audio_clips.insert(0, AudioFileClip(str(bed)))
    video = video.with_audio(CompositeAudioClip(audio_clips).with_duration(total))

    out = job_dir / ("preview_no_music.mp4" if preview_without_music else "final.mp4")
    partial = out.with_name(out.stem + ".part.mp4")  # a crash mid-render must not leave a "finished" file
    video.write_videofile(
        str(partial), fps=FPS, codec="libx264", audio_codec="aac", audio_bitrate="192k",
        preset="veryfast", threads=os.cpu_count(), pixel_format="yuv420p",
        ffmpeg_params=["-crf", "20", "-movflags", "+faststart"], logger=None,
    )
    for r in readers + audio_clips:
        r.close()
    os.replace(partial, out)
    (job_dir / "music_bed.wav").unlink(missing_ok=True)

    if not preview_without_music:  # a preview doesn't count toward variety rotation
        status.update(caption_style=style_name, music=track_name)
        write_json(job_dir / "status.json", status)
    log.info("Rendered %s (%.1fs)", out, total)
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Render final.mp4 for one job folder")
    parser.add_argument("job_dir", type=Path)
    parser.add_argument("--preview-without-music", action="store_true",
                        help="render preview_no_music.mp4 to check visuals before music is added")
    args = parser.parse_args()
    run(args.job_dir, args.preview_without_music)
