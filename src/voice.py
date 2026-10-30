"""Voiceover with Kokoro TTS.

Writes into the job folder:
  voice/scene_NN.wav  one file per scene
  voice.wav           all scenes joined with a short pause, loudness-normalised
  voice.json          {sample_rate, duration, scenes: [{index, start, end}]}
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from .common import CONFIG, get_logger, read_json, write_json

log = get_logger()

SAMPLE_RATE = 24000
SCENE_PAUSE = 0.35  # seconds of silence between scenes, so the read doesn't run on
TARGET_LUFS = -16
_pipelines = {}


def _pipeline(voice: str):
    from kokoro import KPipeline  # heavy import; only when synthesising

    lang = voice[0]  # Kokoro voice ids start with their language code (a, b, h, ...)
    if lang not in _pipelines:
        _pipelines[lang] = KPipeline(lang_code=lang, repo_id="hexgrad/Kokoro-82M")
    return _pipelines[lang]


def synthesize(text: str, voice: str, speed: float) -> np.ndarray:
    chunks = [np.asarray(audio) for _, _, audio in _pipeline(voice)(text, voice=voice, speed=speed)]
    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


def loudnorm(src: Path, dst: Path) -> None:
    """Two-pass FFmpeg loudnorm to TARGET_LUFS (more accurate than single pass)."""
    target = f"I={TARGET_LUFS}:TP=-1.5:LRA=11"
    probe = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-af",
         f"loudnorm={target}:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    m = json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", probe.stderr).group(0))
    second = (f"loudnorm={target}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
              f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
              f"offset={m['target_offset']}:linear=true")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-af", second,
         "-ar", str(SAMPLE_RATE), "-ac", "1", str(dst)],
        check=True,
    )


def run(job_dir: Path) -> dict:
    script = read_json(job_dir / "script.json")
    voice, speed = CONFIG["channel"]["voice"], CONFIG["channel"]["speed"]
    scene_dir = job_dir / "voice"
    scene_dir.mkdir(exist_ok=True)
    pause = np.zeros(int(SCENE_PAUSE * SAMPLE_RATE), dtype=np.float32)

    parts, timings, cursor = [], [], 0.0
    for n, scene in enumerate(script["scenes"]):
        audio = synthesize(scene["narration"], voice, speed).astype(np.float32)
        sf.write(scene_dir / f"scene_{n:02d}.wav", audio, SAMPLE_RATE)
        if n:
            parts.append(pause)
            cursor += SCENE_PAUSE
        start = cursor
        cursor += len(audio) / SAMPLE_RATE
        timings.append({"index": n, "start": round(start, 3), "end": round(cursor, 3)})
        parts.append(audio)

    raw = job_dir / "voice_raw.wav"
    sf.write(raw, np.concatenate(parts), SAMPLE_RATE)
    loudnorm(raw, job_dir / "voice.wav")
    raw.unlink()

    info = {"sample_rate": SAMPLE_RATE, "voice": voice, "speed": speed,
            "duration": round(cursor, 3), "scenes": timings}
    write_json(job_dir / "voice.json", info)
    log.info("Voice: %s, %.1fs across %d scenes", job_dir.name, cursor, len(timings))
    return info


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Synthesize voice.wav for one job folder")
    parser.add_argument("job_dir", type=Path)
    run(parser.parse_args().job_dir)
