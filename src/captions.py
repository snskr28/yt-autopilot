"""Word-level captions with faster-whisper.

captions.json: {words: [{word, start, end}], chunks: [{text, start, end, words}]}
Chunks are 2-4 words, broken early at punctuation or a pause.
"""
import argparse
import subprocess
from pathlib import Path

import numpy as np

from .common import get_logger, read_json, write_json

log = get_logger()

MODEL_SIZE = "small"
MAX_WORDS = 4
MIN_WORDS = 2
BREAK_GAP = 0.30  # seconds of silence that ends a chunk
SNAP_FRAME = 0.01
SILENCE_DB = 40  # frames this far below the loudest frame count as silence
_model = None


def _whisper():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
    return _model


def _decode(audio: Path) -> np.ndarray:
    """16 kHz mono float32 via FFmpeg. (faster-whisper's own PyAV decoder breaks
    with the installed PyAV version, which dropped the metadata_errors argument.)"""
    pcm = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(audio),
         "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(pcm, dtype=np.float32)


def transcribe(audio: Path, hint: str = "") -> list[dict]:
    pcm = _decode(audio)
    segments, _ = _whisper().transcribe(
        pcm, language="en", word_timestamps=True, vad_filter=False,
        # the script text steers spelling of names and numbers
        initial_prompt=hint[:800] or None,
    )
    words = [
        {"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)}
        for seg in segments for w in seg.words if w.word.strip()
    ]
    return snap_to_speech(words, pcm)


def snap_to_speech(words: list[dict], audio: np.ndarray, sr: int = 16000) -> list[dict]:
    """Whisper tends to start a word early when silence precedes it (scene
    pauses). Move word edges that sit in silence onto the actual speech."""
    hop = int(SNAP_FRAME * sr)
    frames = audio[: len(audio) // hop * hop].reshape(-1, hop)
    db = 20 * np.log10(np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-9)
    active = db > db.max() - SILENCE_DB

    def is_active(t):
        i = int(t / SNAP_FRAME)
        return 0 <= i < len(active) and active[i]

    for w in words:
        start, end = w["start"], w["end"]
        while start < end - SNAP_FRAME and not is_active(start):
            start += SNAP_FRAME
        while end > start + SNAP_FRAME and not is_active(end - SNAP_FRAME):
            end -= SNAP_FRAME
        w["start"], w["end"] = round(start, 3), round(end, 3)
    return words


def chunk(words: list[dict]) -> list[dict]:
    chunks, current = [], []

    def flush():
        if current:
            chunks.append({"text": " ".join(w["word"] for w in current),
                           "start": current[0]["start"], "end": current[-1]["end"],
                           "words": list(current)})
            current.clear()

    for i, word in enumerate(words):
        if current and len(current) >= MIN_WORDS and word["start"] - current[-1]["end"] > BREAK_GAP:
            flush()
        current.append(word)
        ends_phrase = word["word"][-1] in ".,!?;:"
        if len(current) >= MAX_WORDS or (ends_phrase and len(current) >= MIN_WORDS):
            flush()
    flush()
    return chunks


def run(job_dir: Path) -> dict:
    script = read_json(job_dir / "script.json")
    hint = " ".join(s["narration"] for s in script["scenes"])
    words = transcribe(job_dir / "voice.wav", hint)
    data = {"model": MODEL_SIZE, "words": words, "chunks": chunk(words)}
    write_json(job_dir / "captions.json", data)
    log.info("Captions: %s, %d words in %d chunks", job_dir.name, len(words), len(data["chunks"]))
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Write captions.json for one job folder")
    parser.add_argument("job_dir", type=Path)
    run(parser.parse_args().job_dir)
