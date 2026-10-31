"""Stock visuals from free sources.

Candidates per query, interleaved so Claude sees every source:
  videos: Pexels* + Pixabay* + Wikimedia Commons
  photos: Pexels* + Pixabay* + NASA + Commons        (* only if the key is in .env)
Pixabay is strong on people and everyday scenes; Commons and NASA on science and space.

Commons and NASA need no key. Commons files are used only under public domain,
CC0 or CC BY (no NC/ND/SA) and with no extra restrictions; every source's
author, licence and page are recorded for the description credits.

For every scene a handful of video + photo candidates are gathered, then one
`claude -p` call picks the best ones from their titles/descriptions (it can't
see the files). Each scene gets up to ceil(span / MAX_SHOT) shots so the
picture keeps moving. Only clips Claude approves are ever used. Scenes short of
approved clips get a re-query round (Claude suggests other searches, then picks
again); any slot still empty becomes a generated motion card (kind "motion")
with the scene's key phrase. If Claude is unavailable the step fails and is
retried later. Writes clips/, visuals_plan.json (resume point) and visuals.json:
  {shots: [{scene, start, end, kind, source, id, file, author, author_url, license, page_url}]}
Never reuses a clip used in the last `variety.clip_reuse_window_videos` videos.
"""
import argparse
import html
import json
import math
import os
import re
import subprocess
import time
from itertools import zip_longest
from pathlib import Path

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .claude_cli import ClaudeError, UsageLimitError, ask
from .common import CONFIG, ROOT, fill_prompt, get_logger, read_json, write_json
from .jobs import history

load_dotenv(ROOT / ".env")
log = get_logger()

# One session for every request: retries dropped connections and 429/5xx with
# backoff (honouring Retry-After). A long video makes hundreds of requests.
HTTP = requests.Session()
HTTP.mount("https://", HTTPAdapter(max_retries=Retry(
    total=6, connect=6, read=4, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=("GET",), respect_retry_after_header=True, raise_on_status=False)))

MAX_SHOT = {"short": 6.0, "long": 5.0}
TAIL = 0.5  # seconds of picture after the last word
TIMEOUT = 60
# Wikimedia's User-Agent policy requires a way to contact the operator.
USER_AGENT = f"yt-autopilot/0.1 (personal YouTube video pipeline; {os.environ.get('CONTACT_EMAIL', 'no contact set')})"
DOWNLOAD_PAUSE = 2.0  # seconds between file downloads, to stay well inside rate limits
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
MIN_VIDEO_HEIGHT = 720
MAX_VIDEO_SECONDS = 180  # we download whole files, so skip very long ones
PICK_VIDEOS, PICK_PHOTOS = 6, 4  # candidates per scene shown to Claude
PICK_BATCH = 8                   # scenes per Claude call (long videos have ~60 scenes)
TARGET_SHOT_LONG = 4.0           # plan: a new visual every 3-5 s in long videos
CARD_SECONDS = 4.0               # full-screen text card for a key fact (long videos)
CARD_MIN_GAP = 25.0              # at most one card every this many seconds
MIN_PHOTO_SIDE = 1200
# words too generic to prove a result is about the query
GENERIC = {"the", "a", "an", "of", "and", "in", "on", "with", "close", "up", "closeup", "view",
           "shot", "footage", "video", "time", "lapse", "timelapse", "space", "background",
           "slow", "motion", "macro", "aerial", "night", "day", "sky"}


class VisualsError(RuntimeError):
    pass


def recent_clip_ids() -> set[str]:
    window = CONFIG["variety"]["clip_reuse_window_videos"]
    used = set()
    for item in history()[-window:]:
        used.update(item.get("clip_ids", []))
    return used


def _specific_words(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", query.lower())
    specific = [w for w in words if w not in GENERIC]
    # an all-generic query ("day night time lapse") still has to match something
    return specific or [w for w in words if w not in {"the", "a", "an", "of", "and", "in", "on", "with"}]


def _relevant(query: str, text: str) -> int:
    """How many specific query words appear in the result's title/keywords."""
    text = text.lower()
    return sum(1 for w in _specific_words(query) if re.search(rf"\b{re.escape(w)}", text))


def _rank(query: str, items: list[dict]) -> list[dict]:
    """Keep results whose title/categories mention every specific query word.

    Strict on purpose: _queries() already retries with shorter queries, so
    "Venus planet space" falls back to "Venus planet", then "Venus".
    """
    needed = len(_specific_words(query))
    if not needed:
        return items
    return [i for i in items if _relevant(query, i.get("match_text", "")) == needed]


# ---------- Pexels / Pixabay (optional, need keys) ----------

def _slug_words(url: str) -> str:
    """Pexels page URLs carry a description slug: /video/aerial-view-of-a-beach-12345/"""
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"-?\d+$", "", slug).replace("-", " ")


def _pexels(path: str, params: dict) -> dict | None:
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return None
    r = HTTP.get(f"https://api.pexels.com/{path}", params=params,
                     headers={"Authorization": key}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _best_video_file(files: list[dict], w: int, h: int) -> dict | None:
    """Smallest file that still covers the target frame; else the largest."""
    usable = [f for f in files if f.get("file_type") == "video/mp4" and f.get("width") and f.get("height")]
    covering = [f for f in usable if f["width"] >= w * 0.95 and f["height"] >= h * 0.95]
    if covering:
        return min(covering, key=lambda f: f["width"] * f["height"])
    return max(usable, key=lambda f: f["width"] * f["height"], default=None)


def pexels_videos(query: str, orientation: str, w: int, h: int) -> list[dict]:
    data = _pexels("videos/search", {"query": query, "orientation": orientation,
                                     "size": "medium", "per_page": 15})
    out = []
    for v in (data or {}).get("videos", []):
        f = _best_video_file(v.get("video_files", []), w, h)
        if f:
            out.append({"kind": "video", "source": "pexels", "id": f"pexels-video-{v['id']}",
                        "url": f["link"], "duration": v.get("duration", 0), "license": "Pexels License",
                        "about": _slug_words(v["url"]),
                        "author": v["user"]["name"], "author_url": v["user"]["url"], "page_url": v["url"]})
    return out


def pexels_photos(query: str, orientation: str) -> list[dict]:
    data = _pexels("v1/search", {"query": query, "orientation": orientation, "per_page": 15})
    return [{"kind": "photo", "source": "pexels", "id": f"pexels-photo-{p['id']}",
             "url": p["src"]["large2x"], "duration": None, "license": "Pexels License",
             "about": p.get("alt") or _slug_words(p["url"]),
             "author": p["photographer"], "author_url": p["photographer_url"], "page_url": p["url"]}
            for p in (data or {}).get("photos", [])]


def _pixabay(path: str, params: dict) -> dict | None:
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return None
    r = HTTP.get(f"https://pixabay.com/api/{path}", timeout=TIMEOUT,
                 params={"key": key, "safesearch": "true", "per_page": 15, **params})
    r.raise_for_status()
    return r.json()


def _pixabay_author(hit: dict) -> dict:
    return {"author": hit["user"], "license": "Pixabay Content License",
            "author_url": f"https://pixabay.com/users/{hit['user']}-{hit['user_id']}/",
            "page_url": hit["pageURL"]}


def pixabay_videos(query: str, w: int, h: int) -> list[dict]:
    """Strong on people, hands, eyes and everyday scenes, where the free archives are thin.
    AI-generated clips are skipped; the smallest rendition that covers the frame is used."""
    data = _pixabay("videos/", {"q": query})
    out = []
    for v in (data or {}).get("hits", []):
        if v.get("isAiGenerated"):
            continue
        files = [f for f in v.get("videos", {}).values() if f.get("url") and f.get("height")]
        covering = [f for f in files if min(f["width"], f["height"]) >= min(w, h) * 0.95]
        best = (min(covering, key=lambda f: f["width"] * f["height"]) if covering
                else max(files, key=lambda f: f["width"] * f["height"], default=None))
        if not best or min(best["width"], best["height"]) < MIN_VIDEO_HEIGHT:
            continue
        out.append({"kind": "video", "source": "pixabay", "id": f"pixabay-video-{v['id']}",
                    "url": best["url"], "duration": v.get("duration", 0),
                    "about": f"Pixabay video. Tags: {v.get('tags', '')}", **_pixabay_author(v)})
    return out


def pixabay_photos(query: str, orientation: str) -> list[dict]:
    data = _pixabay("", {"q": query, "image_type": "photo",
                         "orientation": "vertical" if orientation == "portrait" else "horizontal"})
    out = []
    for p in (data or {}).get("hits", []):
        if p.get("isAiGenerated") or p.get("isLowQuality") or min(p["imageWidth"], p["imageHeight"]) < MIN_PHOTO_SIDE:
            continue
        out.append({"kind": "photo", "source": "pixabay", "id": f"pixabay-photo-{p['id']}",
                    "url": p["largeImageURL"], "duration": None,
                    "about": f"Pixabay photo. Tags: {p.get('tags', '')}", **_pixabay_author(p)})
    return out


def _interleave(*lists: list[dict]) -> list[dict]:
    """Alternate sources so the candidate cut-off never drops a whole source."""
    out, seen = [], set()
    for group in zip_longest(*lists):
        for item in group:
            if item and item["id"] not in seen:
                seen.add(item["id"])
                out.append(item)
    return out


# ---------- Wikimedia Commons (no key) ----------

def _strip_html(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def _commons_license_ok(meta: dict) -> str | None:
    """Return the licence name if usable on a monetised channel, else None."""
    lic = (meta.get("License", {}).get("value") or "").lower()
    if meta.get("Restrictions", {}).get("value"):
        return None  # trademark, personality rights, etc.
    if lic.startswith("pd") or lic == "cc0" or re.fullmatch(r"cc-by-\d(\.\d)?", lic):
        return meta.get("LicenseShortName", {}).get("value") or lic
    return None


COMMONS_META = "License|LicenseShortName|Artist|Restrictions|Categories|ImageDescription"


def _commons_search(query: str, filetype: str, extra: dict) -> list[dict]:
    params = {"action": "query", "format": "json", "formatversion": 2, "generator": "search",
              "gsrsearch": f"{query} filetype:{filetype}", "gsrnamespace": 6, "gsrlimit": 20,
              "viextmetadatafilter": COMMONS_META, "iiextmetadatafilter": COMMONS_META, **extra}
    r = HTTP.get(COMMONS_API, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    r.raise_for_status()
    pages = r.json().get("query", {}).get("pages", [])
    return sorted(pages, key=lambda p: p.get("index", 0))


def _commons_item(page: dict, info: dict, kind: str) -> dict | None:
    meta = info.get("extmetadata", {})
    license_name = _commons_license_ok(meta)
    if not license_name:
        return None
    title = page["title"].removeprefix("File:")
    categories = meta.get("Categories", {}).get("value", "")
    description = _strip_html(meta.get("ImageDescription", {}).get("value"))
    return {"kind": kind, "source": "commons", "id": f"commons-{page['pageid']}",
            "license": license_name, "author": _strip_html(meta.get("Artist", {}).get("value")) or "Unknown",
            "author_url": info.get("descriptionurl"), "page_url": info.get("descriptionurl"),
            "match_text": f"{title} {categories}",
            "about": f"{title}. {description[:240]} [categories: {categories.replace('|', ', ')[:200]}]"}


def commons_videos(query: str, w: int, h: int) -> list[dict]:
    out = []
    for page in _commons_search(query, "video", {"prop": "videoinfo",
                                                 "viprop": "url|size|mime|extmetadata|derivatives"}):
        info = (page.get("videoinfo") or [{}])[0]
        if (not info.get("duration") or info["duration"] > MAX_VIDEO_SECONDS
                or min(info.get("width", 0), info.get("height", 0)) < MIN_VIDEO_HEIGHT):
            continue
        item = _commons_item(page, info, "video")
        if not item:
            continue
        # smallest transcode that still covers the frame's short side
        need = min(w, h)
        derivs = [d for d in info.get("derivatives", []) if "webm" in d.get("type", "") and d.get("height")]
        covering = [d for d in derivs if min(d["width"], d["height"]) >= need]
        best = (min(covering, key=lambda d: d["height"]) if covering
                else max(derivs, key=lambda d: d["height"], default=None))
        item.update(url=best["src"] if best else info["url"], duration=info["duration"])
        out.append(item)
    return _rank(query, out)


def commons_photos(query: str) -> list[dict]:
    out = []
    for page in _commons_search(query, "bitmap", {"prop": "imageinfo", "iiurlwidth": 3000,
                                                  "iiprop": "url|size|mime|extmetadata"}):
        info = (page.get("imageinfo") or [{}])[0]
        if min(info.get("width", 0), info.get("height", 0)) < MIN_PHOTO_SIDE:
            continue
        item = _commons_item(page, info, "photo")
        if item:
            item.update(url=info.get("thumburl") or info["url"], duration=None)
            out.append(item)
    return _rank(query, out)


# ---------- NASA Image and Video Library (no key, public domain) ----------

def nasa_photos(query: str) -> list[dict]:
    r = HTTP.get("https://images-api.nasa.gov/search", timeout=TIMEOUT,
                     params={"q": query, "media_type": "image", "page_size": 30})
    r.raise_for_status()
    out = []
    for item in r.json().get("collection", {}).get("items", []):
        d = item["data"][0]
        nasa_id = d["nasa_id"]
        out.append({
            "kind": "photo", "source": "nasa", "id": f"nasa-{nasa_id}",
            "url": f"https://images-assets.nasa.gov/image/{nasa_id}/{nasa_id}~large.jpg",
            # not every NASA asset has a ~large rendition
            "alt_urls": [f"https://images-assets.nasa.gov/image/{nasa_id}/{nasa_id}~{size}.jpg"
                         for size in ("medium", "orig")],
            "duration": None, "license": "Public domain (NASA)",
            "author": f"NASA{(' / ' + d['center']) if d.get('center') else ''}",
            "author_url": "https://images.nasa.gov/", "page_url": f"https://images.nasa.gov/details/{nasa_id}",
            "match_text": f"{d.get('title', '')} {' '.join(d.get('keywords', []))}",
            "about": f"{d.get('title', '')}. {_strip_html(d.get('description', ''))[:240]}",
        })
    return _rank(query, out)


# ---------- search + download ----------

def _queries(query: str) -> list[str]:
    """The scene query, then progressively shorter versions of it."""
    words = query.split()
    return [" ".join(words[:n]) for n in range(len(words), 0, -1)]


def _per_source(query: str, search, keep=lambda item: True) -> list[dict]:
    """One source's results for the fullest wording of the query that finds anything.
    Each source shortens the query on its own: a loose keyword search (Pixabay) always
    matches the full wording, a strict one (Commons, NASA) often only a shorter one."""
    for q in _queries(query):
        found = [item for item in search(q) if keep(item)]
        if found:
            return found
    return []


def find_photos(query: str, orientation: str) -> list[dict]:
    return _interleave(_per_source(query, lambda q: pexels_photos(q, orientation)),
                       _per_source(query, lambda q: pixabay_photos(q, orientation)),
                       _per_source(query, nasa_photos),
                       _per_source(query, commons_photos))


def find_videos(query: str, orientation: str, need: float, w: int, h: int) -> list[dict]:
    long_enough = lambda v: v["duration"] >= need  # noqa: E731
    return _interleave(_per_source(query, lambda q: pexels_videos(q, orientation, w, h), long_enough),
                       _per_source(query, lambda q: pixabay_videos(q, w, h), long_enough),
                       _per_source(query, lambda q: commons_videos(q, w, h), long_enough))


def claude_picks(scenes: list[dict]) -> dict[int, list[str]] | None:
    """Claude chooses clips, PICK_BATCH scenes per call. None if Claude is unavailable."""
    about_len = 400 if len(scenes) <= PICK_BATCH else 220
    picks: dict[int, list[str]] = {}
    taken: set[str] = set()
    for i in range(0, len(scenes), PICK_BATCH):
        batch = scenes[i:i + PICK_BATCH]
        payload = [{"scene": s["scene"], "narration": s["narration"], "shots_needed": s["count"],
                    "candidates": [{"id": c["id"], "kind": c["kind"], "about": c.get("about", "")[:about_len]}
                                   for c in s["items"] if c["id"] not in taken]}
                   for s in batch]
        prompt = fill_prompt("pick_visuals", niche=CONFIG["channel"]["niche"],
                             scenes_json=json.dumps(payload, ensure_ascii=False))
        try:
            reply = ask(prompt, '{"scenes": [{"scene": int, "picks": [str]}]}')
        except UsageLimitError:
            log.warning("Claude usage limit hit during clip picks")
            return None
        except ClaudeError as e:
            log.warning("Claude clip pick failed for one batch (%s); those slots become motion cards", e)
            continue
        for s in reply.get("scenes", []):
            ids = [str(p) for p in s.get("picks", [])]
            picks[int(s["scene"])] = ids
            taken.update(ids)
    return picks


def _fetch(url: str, path: Path) -> None:
    """Plain streamed download, with polite retries when the server says 429/503."""
    time.sleep(DOWNLOAD_PAUSE)
    for attempt in range(5):
        with HTTP.get(url, headers={"User-Agent": USER_AGENT}, stream=True, timeout=TIMEOUT) as r:
            if r.status_code in (429, 503) and attempt < 4:
                wait = int(r.headers.get("Retry-After", "") or 0) or 5 * 2 ** attempt
                log.info("Rate limited by %s; waiting %ds", url.split("/")[2], wait)
                time.sleep(min(wait, 120))
                continue
            r.raise_for_status()
            tmp = path.with_name(path.name + ".part")
            with open(tmp, "wb") as f:
                for block in r.iter_content(1 << 20):
                    f.write(block)
            os.replace(tmp, path)
            return


def _video_seconds(path: Path) -> float:
    """Duration per ffprobe, or 0 if the file is unreadable."""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def download(item: dict, dest: Path, need: float) -> tuple[Path, float]:
    """Fetch a photo, or a video cut down to the needed seconds. Returns (path, offset)."""
    if item["kind"] == "photo":
        path = dest / f"{item['id']}.jpg"
        urls = [item["url"]] + item.get("alt_urls", [])
        for n, url in enumerate(urls):
            if path.exists():
                break
            try:
                _fetch(url, path)
            except requests.HTTPError:
                if n == len(urls) - 1:
                    raise
        return path, 0.0
    # Skip intros/title cards in long clips: start 15% in, as long as enough remains.
    offset = round(max(0.0, min(item["duration"] * 0.15, item["duration"] - need - 0.2)), 2)
    path = dest / f"{item['id']}_{offset:g}s.mp4"
    if path.exists() and _video_seconds(path) < need:
        path.unlink()  # broken or short leftover from an interrupted run
    if not path.exists():
        full = dest / f"{item['id']}.source"
        _fetch(item["url"], full)
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{offset}", "-i", str(full),
             "-t", f"{need + 0.5:.2f}", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
             "-pix_fmt", "yuv420p", str(path)],
            check=True, timeout=600,
        )
        full.unlink()
    return path, offset


def scene_spans(scene_times: list[dict], total: float) -> list[tuple[float, float]]:
    """Each scene stays on screen until the next one starts (the first from 0)."""
    return [(0.0 if i == 0 else s["start"],
             scene_times[i + 1]["start"] if i + 1 < len(scene_times) else total)
            for i, s in enumerate(scene_times)]


def _fits(item: dict, seconds: float) -> bool:
    return item["duration"] is None or item["duration"] >= seconds


def run(job_dir: Path) -> dict:
    status = read_json(job_dir / "status.json")
    script = read_json(job_dir / "script.json")
    voice = read_json(job_dir / "voice.json")
    video_type = status["type"]
    w, h = CONFIG["formats"][video_type]["resolution"]
    orientation = "portrait" if video_type == "short" else "landscape"
    total = voice["duration"] + TAIL
    banned = recent_clip_ids()

    # Steps 1-2 are slow (hundreds of searches + Claude calls), so their result is
    # saved and reused if a later download fails and the step is re-run.
    plan_path = job_dir / "visuals_plan.json"
    plan = read_json(plan_path)
    if plan and plan.get("voice_duration") == voice["duration"] and plan.get("picks") is not None:
        scenes, cards = plan["scenes"], plan["cards"]
        picks = {int(k): v for k, v in plan["picks"].items()}
        log.info("Reusing saved search results and picks from %s", plan_path.name)
    else:
        scenes, cards = search_scenes(script, voice, video_type, total, orientation, w, h, banned)
        picks = claude_picks(scenes)  # 2. Claude chooses from the candidates' descriptions
        if picks is None:
            # never guess without Claude: unapproved clips can be badly wrong
            raise VisualsError("Claude unavailable for clip picks; run visuals again later")
        plan = {"voice_duration": voice["duration"], "scenes": scenes, "cards": cards, "picks": picks}
        write_json(plan_path, plan)
    if not plan.get("requeried"):
        requery(scenes, picks, script, orientation, w, h, banned)
        plan.update(scenes=scenes, picks=picks, requeried=True)
        write_json(plan_path, plan)
    shots = build_shot_list(job_dir, scenes, picks)
    shots = sorted(shots + cards, key=lambda s: s["start"])
    data = {"duration": round(total, 3), "shots": shots}
    write_json(job_dir / "visuals.json", data)
    return data


def search_scenes(script, voice, video_type, total, orientation, w, h, banned) -> tuple[list, list]:
    """1. search: a few videos and photos per scene (long videos: maybe a text card first)."""
    scenes, cards, last_card = [], [], -CARD_MIN_GAP
    video_cache: dict[str, list[dict]] = {}
    photo_cache: dict[str, list[dict]] = {}
    for i, (a, b) in enumerate(scene_spans(voice["scenes"], total)):
        scene = script["scenes"][i]
        if (video_type == "long" and scene.get("on_screen_text") and a - last_card >= CARD_MIN_GAP
                and b - a >= CARD_SECONDS + 3):
            cards.append({"scene": i, "start": round(a, 3), "end": round(a + CARD_SECONDS, 3),
                          "kind": "card", "text": scene["on_screen_text"]})
            last_card = a
            a += CARD_SECONDS
        query = scene["visual_query"]
        if i % 10 == 0:
            log.info("Searching visuals: scene %d/%d", i + 1, len(script["scenes"]))
        if video_type == "long":
            count = max(1, round((b - a) / TARGET_SHOT_LONG))
        else:
            count = max(1, math.ceil((b - a) / MAX_SHOT[video_type]))
        if query not in video_cache:
            video_cache[query] = find_videos(query, orientation, 3.0, w, h)
            photo_cache[query] = find_photos(query, orientation)
        videos = [v for v in video_cache[query] if v["id"] not in banned and v["duration"] >= (b - a) / count]
        photos = [p for p in photo_cache[query] if p["id"] not in banned]
        items = list({c["id"]: c for c in videos[:PICK_VIDEOS] + photos[:PICK_PHOTOS]}.values())
        if not items:
            raise VisualsError(f"no stock visual found for scene {i} query {query!r}")
        scenes.append({"scene": i, "span": (a, b), "count": count, "items": items,
                       "narration": scene["narration"]})
    return scenes, cards


def requery(scenes: list[dict], picks: dict, script: dict, orientation: str, w: int, h: int,
            banned: set[str]) -> None:
    """2b. Scenes short of approved clips: Claude suggests other queries, we search
    again, Claude picks again. Also stores each such scene's card_text. In place."""
    short_of = [s for s in scenes if len(picks.get(s["scene"], [])) < s["count"]]
    if not short_of:
        return
    log.info("%d scenes are short of approved clips; asking Claude for other searches", len(short_of))
    ideas: dict[int, dict] = {}
    for i in range(0, len(short_of), 15):
        batch = [{"scene": s["scene"], "narration": s["narration"],
                  "first_query": script["scenes"][s["scene"]]["visual_query"]} for s in short_of[i:i + 15]]
        try:
            reply = ask(fill_prompt("requery_visuals", niche=CONFIG["channel"]["niche"],
                                    scenes_json=json.dumps(batch, ensure_ascii=False)),
                        '{"scenes": [{"scene": int, "queries": [str], "card_text": str}]}')
        except ClaudeError as e:
            log.warning("Re-query failed (%s); remaining gaps become motion cards", e)
            continue
        ideas.update({int(x["scene"]): x for x in reply.get("scenes", [])})

    taken = {p for ids in picks.values() for p in ids}
    for s in short_of:
        idea = ideas.get(s["scene"], {})
        s["card_text"] = idea.get("card_text") or script["scenes"][s["scene"]].get("on_screen_text") or ""
        a, b = s["span"]
        found: dict[str, dict] = {}
        for q in idea.get("queries", [])[:3]:
            for c in (find_videos(q, orientation, (b - a) / s["count"], w, h)[:4] + find_photos(q, orientation)[:3]):
                if c["id"] not in banned and c["id"] not in taken:
                    found.setdefault(c["id"], c)
        s["new_items"] = list(found.values())[:PICK_VIDEOS + PICK_PHOTOS]
    retry = [{**s, "items": s["new_items"],
              "count": s["count"] - len(picks.get(s["scene"], []))} for s in short_of if s.get("new_items")]
    more = claude_picks(retry) or {}
    for s in short_of:
        new = more.get(s["scene"], [])
        if new:
            s["items"] = s["items"] + [c for c in s["new_items"] if c["id"] in new]
            picks[s["scene"]] = picks.get(s["scene"], []) + new
        s.pop("new_items", None)
    still = sum(1 for s in short_of if len(picks.get(s["scene"], [])) < s["count"])
    log.info("Re-query filled %d scenes; %d still short (motion cards fill the gaps)",
             len(short_of) - still, still)


def build_shot_list(job_dir: Path, scenes: list[dict], picks: dict) -> list[dict]:
    """3. build shots. Only Claude-approved clips are used; every slot a scene
    can't fill with one becomes a generated motion card (kind "motion")."""
    clips_dir = job_dir / "clips"
    clips_dir.mkdir(exist_ok=True)
    used: set[str] = set()
    shots = []
    for s in scenes:
        a, b = s["span"]
        step = (b - a) / s["count"]
        by_id = {c["id"]: c for c in s["items"]}
        approved = [by_id[p] for p in picks.get(s["scene"], []) if p in by_id]
        for k in range(s["count"]):
            shot = {"scene": s["scene"], "start": round(a + k * step, 3), "end": round(a + (k + 1) * step, 3)}
            pick = path = None
            for candidate in approved:
                if candidate["id"] in used or not _fits(candidate, step):
                    continue
                try:
                    path, offset = download(candidate, clips_dir, step)
                except (requests.RequestException, subprocess.SubprocessError) as e:
                    log.warning("Download failed for %s (%s); trying another approved clip", candidate["id"], e)
                    continue
                pick = candidate
                break
            if pick is None:
                shot.update(kind="motion", text=s.get("card_text") or "", variant=k)
                shots.append(shot)
                log.info("Shot %.1f-%.1fs scene %d: motion card %r", shot["start"], shot["end"],
                         s["scene"], shot["text"])
                continue
            used.add(pick["id"])
            shot.update({key: pick[key] for key in ("kind", "source", "id", "author", "author_url",
                                                   "license", "page_url")})
            shot.update(file=str(path.relative_to(job_dir)), source_offset=offset, about=pick.get("about", ""))
            shots.append(shot)
            log.info("Shot %.1f-%.1fs scene %d: %s %s by %s (%s)", shot["start"], shot["end"],
                     s["scene"], pick["kind"], pick["id"], pick["author"], pick["license"])
    return shots


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch stock visuals for one job folder")
    parser.add_argument("job_dir", type=Path)
    run(parser.parse_args().job_dir)
