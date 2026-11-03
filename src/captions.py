"""Word-level captions: the script's exact words, timed with faster-whisper.

The script is the source of truth for the text, so a word Whisper misses or mishears
still gets its caption. Whisper only supplies timing: script words it heard take its
timestamps, the rest are spread through the gaps by length, inside the scene's own
speech (from voice.json and the waveform).

captions.json: {model, aligned, words: [{word, start, end}], chunks: [{text, start, end, words}]}
Chunks are 2-4 words, broken early at punctuation or a pause. `aligned` is the share of
script words Whisper confirmed; a low value means the timing is mostly estimated.
"""
import argparse
import difflib
import re
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
MIN_ALIGNED = 0.70  # warn below this share of Whisper-confirmed words
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


def transcribe(pcm: np.ndarray) -> list[dict]:
    """What Whisper hears, with timestamps. No initial_prompt: passing the script as one made
    Whisper treat it as already said and drop most of the speech (30 of 120 words on one video)."""
    segments, _ = _whisper().transcribe(
        pcm, language="en", word_timestamps=True, vad_filter=False, condition_on_previous_text=False,
    )
    return [
        {"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)}
        for seg in segments for w in seg.words if w.word.strip()
    ]


def _active_frames(audio: np.ndarray, sr: int = 16000) -> np.ndarray:
    hop = int(SNAP_FRAME * sr)
    frames = audio[: len(audio) // hop * hop].reshape(-1, hop)
    db = 20 * np.log10(np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-9)
    return db > db.max() - SILENCE_DB


def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", word.lower())


def align_scene(tokens: list[str], heard: list[dict], span: tuple[float, float]) -> tuple[list[dict], int]:
    """Time one scene's script words from the words Whisper heard in it. Returns (words, matched)."""
    times: list[tuple[float, float] | None] = [None] * len(tokens)
    matcher = difflib.SequenceMatcher(None, [_norm(t) for t in tokens], [_norm(w["word"]) for w in heard],
                                      autojunk=False)
    matched = 0
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            w = heard[block.b + k]
            times[block.a + k] = (w["start"], w["end"])
            matched += 1
    # Spread each unmatched run through its gap, in proportion to word length.
    i = 0
    while i < len(tokens):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(tokens) and times[j] is None:
            j += 1
        left = times[i - 1][1] if i else span[0]
        right = times[j][0] if j < len(tokens) else span[1]
        weights = [len(t) + 1 for t in tokens[i:j]]
        cursor, per_weight = left, max(right - left, 0.0) / sum(weights)
        for k, weight in zip(range(i, j), weights):
            times[k] = (cursor, cursor + weight * per_weight)
            cursor += weight * per_weight
        i = j
    return [{"word": t, "start": round(a, 3), "end": round(b, 3)} for t, (a, b) in zip(tokens, times)], matched


def align(script: dict, voice: dict, heard: list[dict], pcm: np.ndarray) -> tuple[list[dict], float]:
    """Every script word with a time, plus the share of words Whisper confirmed."""
    active = _active_frames(pcm)
    words, matched, total = [], 0, 0
    for scene, timing in zip(script["scenes"], voice["scenes"]):
        a, b = timing["start"], timing["end"]
        frames = np.nonzero(active[int(a / SNAP_FRAME): int(b / SNAP_FRAME) + 1])[0]
        span = (a + frames[0] * SNAP_FRAME, a + (frames[-1] + 1) * SNAP_FRAME) if len(frames) else (a, b)
        in_scene = [w for w in heard if a - 0.1 <= (w["start"] + w["end"]) / 2 <= b + 0.1]
        tokens = scene["narration"].split()
        scene_words, hits = align_scene(tokens, in_scene, span)
        words += scene_words
        matched += hits
        total += len(tokens)
    return words, matched / max(total, 1)


def snap_to_speech(words: list[dict], audio: np.ndarray) -> list[dict]:
    """Whisper tends to start a word early when silence precedes it (scene
    pauses). Move word edges that sit in silence onto the actual speech."""
    active = _active_frames(audio)

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
    voice = read_json(job_dir / "voice.json")
    pcm = _decode(job_dir / "voice.wav")
    words, aligned = align(script, voice, transcribe(pcm), pcm)
    words = snap_to_speech(words, pcm)
    data = {"model": MODEL_SIZE, "aligned": round(aligned, 3), "words": words, "chunks": chunk(words)}
    write_json(job_dir / "captions.json", data)
    log.info("Captions: %s, %d words in %d chunks, %.0f%% timed by Whisper",
             job_dir.name, len(words), len(data["chunks"]), aligned * 100)
    if aligned < MIN_ALIGNED:
        log.warning("Caption timing for %s is mostly estimated (%.0f%% confirmed by Whisper); check it in review",
                    job_dir.name, aligned * 100)
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Write captions.json for one job folder")
    parser.add_argument("job_dir", type=Path)
    run(parser.parse_args().job_dir)
