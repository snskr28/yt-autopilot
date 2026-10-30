"""Script writing.

script.json (owner-editable):
  short: {title, description, tags, scenes: [{narration, visual_query, on_screen_text}], word_count}
  long:  the same plus thumbnail_text, outline, and sections: [heading]; every
         scene carries "section" (index into sections). Section 0 is the
         cold-open hook ("Intro"), the last is the outro.

Long descriptions end with a chapter list that this module owns: estimated
from word counts at generate time, then rewritten from the real voice timings
(update_chapters) once voice.json exists.
"""
import argparse
import json
import re
from pathlib import Path

from .claude_cli import ask
from .common import CONFIG, fill_prompt, get_logger, read_json, word_count, write_json

log = get_logger()

SHORT_WORDS = (100, 130)
SHORT_SCENES = (4, 6)
SHORT_TITLE_MAX = 60
ON_SCREEN_MAX_WORDS = 6
SHORT_SCHEMA = ('{"title": str, "description": str, "tags": [str], '
                '"scenes": [{"narration": str, "visual_query": str, "on_screen_text": str|null}]}')


class ScriptError(RuntimeError):
    pass


def validate_short(script: dict) -> list[str]:
    """Return a list of problems; empty means the script is valid."""
    problems = []
    for key in ("title", "description", "tags", "scenes"):
        if not script.get(key):
            problems.append(f"missing {key}")
    if problems:
        return problems
    scenes = script["scenes"]
    if not SHORT_SCENES[0] <= len(scenes) <= SHORT_SCENES[1]:
        problems.append(f"{len(scenes)} scenes; need {SHORT_SCENES[0]}-{SHORT_SCENES[1]}")
    for n, scene in enumerate(scenes, 1):
        if not scene.get("narration") or not scene.get("visual_query"):
            problems.append(f"scene {n} lacks narration or visual_query")
        text = scene.get("on_screen_text")
        if text and word_count(text) > ON_SCREEN_MAX_WORDS:
            problems.append(f"scene {n} on_screen_text has {word_count(text)} words; max {ON_SCREEN_MAX_WORDS}")
    words = word_count(" ".join(s.get("narration", "") for s in scenes))
    if not SHORT_WORDS[0] <= words <= SHORT_WORDS[1]:
        problems.append(f"narration is {words} words; need {SHORT_WORDS[0]}-{SHORT_WORDS[1]}")
    if len(script["title"]) > SHORT_TITLE_MAX:
        problems.append(f"title is {len(script['title'])} chars; max {SHORT_TITLE_MAX}")
    if scenes and scenes[0].get("narration", "").lower().startswith("did you know"):
        problems.append('hook starts with "Did you know"')
    return problems


def write_short(title_angle: str) -> dict:
    """One Claude call for the whole Short; one corrective retry if it fails validation."""
    prompt = fill_prompt("short_script", title_angle=title_angle, niche=CONFIG["channel"]["niche"])
    script = ask(prompt, SHORT_SCHEMA)
    problems = validate_short(script) if isinstance(script, dict) else ["reply was not a JSON object"]
    if problems:
        log.info("Short script failed checks (%s); asking for a fix", "; ".join(problems))
        fix = (f"{prompt}\n\nYour previous draft was:\n{json.dumps(script, ensure_ascii=False)}\n\nIt broke these rules: "
               f"{'; '.join(problems)}. Rewrite it so every rule is met.")
        script = ask(fix, SHORT_SCHEMA)
        problems = validate_short(script) if isinstance(script, dict) else ["reply was not a JSON object"]
        if problems:
            raise ScriptError("script still invalid after retry: " + "; ".join(problems))
    script["word_count"] = word_count(" ".join(s["narration"] for s in script["scenes"]))
    return script


# ---------- long videos ----------

LONG_WORDS = (1200, 1500)
LONG_TARGET = 1350  # middle of LONG_WORDS; section budgets are derived from it
HOOK_WORDS, OUTRO_WORDS = "45-60", "60-80"
MAX_REWRITES = 3
LONG_TITLE_MAX = 70
WORDS_PER_SECOND = 2.55  # measured on Kokoro af_heart at speed 1.0 (Venus Short)
SCENE_PAUSE = 0.35       # matches voice.SCENE_PAUSE
OUTLINE_SCHEMA = '{"hook": str, "sections": [{"heading": str, "points": [str]}], "outro": str}'
SECTION_SCHEMA = '{"scenes": [{"narration": str, "visual_query": str, "on_screen_text": str|null}]}'
META_SCHEMA = '{"title": str, "description": str, "tags": [str], "thumbnail_text": str}'
CHAPTER_LINE = re.compile(r"^\s*(\d{1,2}:)?\d{1,2}:\d{2}\s+\S.*$")


def _section_scenes(n: int, total: int, title: str, outline: dict, tail: str, words: str) -> list[dict]:
    prompt = fill_prompt("long_section", n=n, total=total, title=title, words=words,
                         outline_json=json.dumps(outline, ensure_ascii=False), previous_tail=tail or "(start of video)")
    reply = ask(prompt, SECTION_SCHEMA)
    scenes = reply.get("scenes", []) if isinstance(reply, dict) else []
    scenes = [s for s in scenes if s.get("narration") and s.get("visual_query")]
    if not scenes:
        raise ScriptError(f"section {n} came back empty")
    return scenes


def _tail(scenes: list[dict]) -> str:
    text = " ".join(s["narration"] for s in scenes)
    return " ".join(text.split()[-40:])


def fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    h, m, s = seconds // 3600, seconds // 60 % 60, seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def chapter_starts(script: dict, voice: dict | None = None) -> list[float]:
    """Start time of each section: real (from voice.json) or estimated from words."""
    starts, seen = [], set()
    t = 0.0
    for i, scene in enumerate(script["scenes"]):
        if voice:
            t = voice["scenes"][i]["start"]
        if scene["section"] not in seen:
            seen.add(scene["section"])
            starts.append(0.0 if not starts else t)
        if not voice:
            t += word_count(scene["narration"]) / WORDS_PER_SECOND + SCENE_PAUSE
    return starts


def chapter_block(script: dict, voice: dict | None = None) -> str:
    return "\n".join(f"{fmt_time(t)} {h}" for t, h in zip(chapter_starts(script, voice), script["sections"]))


def set_chapters(description: str, block: str) -> str:
    """Replace any timestamp lines in the description with our chapter list."""
    kept = [l for l in description.splitlines() if not CHAPTER_LINE.match(l) and l.strip() != "Chapters:"]
    return "\n".join(kept).rstrip() + "\n\nChapters:\n" + block


def update_chapters(job_dir: Path) -> str:
    """After voice: rewrite the chapter list with real timestamps."""
    script, voice = read_json(job_dir / "script.json"), read_json(job_dir / "voice.json")
    block = chapter_block(script, voice)
    script["description"] = set_chapters(script["description"], block)
    write_json(job_dir / "script.json", script)
    return block


def write_long(title_angle: str) -> dict:
    """Outline, then one call per section (hook and outro included), then metadata."""
    niche = CONFIG["channel"]["niche"]
    outline = ask(fill_prompt("long_outline", title_angle=title_angle, niche=niche), OUTLINE_SCHEMA)
    if not 5 <= len(outline.get("sections", [])) <= 7:
        raise ScriptError(f"outline has {len(outline.get('sections', []))} sections; need 5-7")

    headings = ["Intro"] + [s["heading"] for s in outline["sections"]] + ["Wrap-up"]
    total = len(headings)
    # The plan's fixed 180-240 per section overshoots 1,500 with 7 sections, so
    # split the total budget across however many sections the outline has.
    share = (LONG_TARGET - 53 - 70) // len(outline["sections"])
    section_words = f"{share - 15}-{share + 20}"
    word_targets = [HOOK_WORDS] + [section_words] * len(outline["sections"]) + [OUTRO_WORDS]
    by_section: list[list[dict]] = []
    for n, words in enumerate(word_targets):
        log.info("Writing section %d/%d: %s", n + 1, total, headings[n])
        tail = _tail(by_section[-1]) if by_section else ""
        by_section.append(_section_scenes(n + 1, total, title_angle, outline, tail, words))

    # length correction: rewrite the body sections furthest from their share, a few at most
    def total_words():
        return sum(word_count(s["narration"]) for sec in by_section for s in sec)

    words_now = total_words()
    rewritten: set[int] = set()
    while not LONG_WORDS[0] <= words_now <= LONG_WORDS[1] and len(rewritten) < MAX_REWRITES:
        sizes = {n: sum(word_count(s["narration"]) for s in by_section[n])
                 for n in range(1, total - 1) if n not in rewritten}
        too_long = words_now > LONG_WORDS[1]
        n = max(sizes, key=sizes.get) if too_long else min(sizes, key=sizes.get)
        missing = (LONG_WORDS[1] - 50 - words_now) if too_long else (LONG_WORDS[0] + 50 - words_now)
        target = max(share - 60, min(share + 80, sizes[n] + missing))  # keep sections reasonable
        log.info("Narration is %d words; rewriting section %d (%d words) to about %d",
                 words_now, n + 1, sizes[n], target)
        by_section[n] = _section_scenes(n + 1, total, title_angle, outline, _tail(by_section[n - 1]),
                                        f"{target - 10}-{target + 10}")
        rewritten.add(n)
        words_now = total_words()
    if not LONG_WORDS[0] <= words_now <= LONG_WORDS[1]:
        raise ScriptError(f"narration is {words_now} words; need {LONG_WORDS[0]}-{LONG_WORDS[1]}")

    scenes = [{**scene, "section": n} for n, sec in enumerate(by_section) for scene in sec]
    script = {"sections": headings, "outline": outline, "scenes": scenes}
    estimate = chapter_block(script)
    meta = ask(fill_prompt("long_metadata", working_title=title_angle,
                           headings_with_timestamps=estimate.replace("\n", "; ")), META_SCHEMA)
    for key in ("title", "description", "tags", "thumbnail_text"):
        if not meta.get(key):
            raise ScriptError(f"metadata missing {key}")
    script.update(
        title=meta["title"][:LONG_TITLE_MAX], description=set_chapters(meta["description"], estimate),
        tags=meta["tags"], thumbnail_text=meta["thumbnail_text"], word_count=words_now,
    )
    return script


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rewrite script.json for one job folder")
    parser.add_argument("job_dir", type=Path)
    parser.add_argument("--chapters", action="store_true",
                        help="long videos: rewrite chapter timestamps from voice.json")
    args = parser.parse_args()
    status = read_json(args.job_dir / "status.json")
    if args.chapters:
        print(update_chapters(args.job_dir))
        raise SystemExit
    script = write_short(status["title_angle"]) if status["type"] == "short" else write_long(status["title_angle"])
    write_json(args.job_dir / "script.json", script)
    print(f"Wrote {args.job_dir / 'script.json'} ({script['word_count']} words)")
