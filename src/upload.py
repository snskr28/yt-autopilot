"""Upload to YouTube with the Data API v3.

  python -m src.upload --login          one-time browser login, saves token.json
  python -m src.upload jobs/<job>       upload final.mp4 (job must contain APPROVED)
  python -m src.upload --verify jobs/<job>   read the uploaded video back from YouTube

Uploads as private with publishAt = the job's slot, made-for-kids false and
the altered/synthetic content flag set. Description gets credits for every
stock source and the music track. Appends the video to data/history.json.
"""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from . import ideas
from .common import CONFIG, DATA_DIR, ROOT, TZ, get_logger, now, read_json, write_json

log = get_logger()

CLIENT_SECRET = ROOT / "client_secret.json"
TOKEN = ROOT / "token.json"
# `youtube` (manage) is needed to set the AI-use disclosure after upload: the flag
# sent with videos.insert was not applied in testing. It also covers reading back.
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube"]
# videos.update replaces the whole status part, so every writable field is sent back
WRITABLE_STATUS = ("privacyStatus", "publishAt", "selfDeclaredMadeForKids", "embeddable",
                   "license", "publicStatsViewable", "containsSyntheticMedia")
MIN_SCHEDULE_LEAD = timedelta(minutes=30)  # YouTube rejects publishAt too close to now
DESCRIPTION_LIMIT = 5000
TAGS_CHAR_LIMIT = 450  # YouTube allows 500 incl. separators; keep a margin


class UploadError(RuntimeError):
    pass


def login() -> None:
    if not CLIENT_SECRET.exists():
        raise UploadError(f"{CLIENT_SECRET.name} not found in {ROOT}")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    log.info("Saved %s", TOKEN.name)


def youtube():
    if not TOKEN.exists():
        raise UploadError("Not logged in. Run: python -m src.upload --login")
    granted = set(read_json(TOKEN, {}).get("scopes") or [])
    if not set(SCOPES) <= granted:
        raise UploadError("Saved login lacks the needed permissions. Run: python -m src.upload --login")
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            # Typical while the OAuth consent screen is in Testing: refresh tokens expire after ~7 days.
            raise UploadError(f"YouTube login expired ({e}). Run: python -m src.upload --login") from e
        TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def credits(job_dir: Path, status: dict) -> str:
    visuals = read_json(job_dir / "visuals.json", {"shots": []})
    lines, seen = [], set()
    for shot in visuals["shots"]:
        if shot["kind"] in ("card", "motion") or shot["id"] in seen:
            continue
        seen.add(shot["id"])
        lines.append(f"- {shot['author']} ({shot['license']}): {shot['page_url']}")
    out = "Footage and images:\n" + "\n".join(lines)
    if status.get("music"):
        out += f"\nMusic: {Path(status['music']).stem}"
    return out


def build_description(script: dict, credit_text: str) -> str:
    text = f"{script['description'].strip()}\n\n{credit_text}"
    if len(text) > DESCRIPTION_LIMIT:
        text = text[:DESCRIPTION_LIMIT - 1] + "…"
    return text


def trim_tags(tags: list[str]) -> list[str]:
    out, used = [], 0
    for tag in tags:
        tag = tag.strip().replace("<", "").replace(">", "")
        cost = len(tag) + (2 if " " in tag else 0) + 1
        if tag and used + cost <= TAGS_CHAR_LIMIT:
            out.append(tag)
            used += cost
    return out


def publish_at(status: dict) -> datetime:
    local = datetime.fromisoformat(f"{status['publish_date']}T{status['publish_time']}").replace(tzinfo=TZ)
    return local.astimezone(timezone.utc)


def upload(job_dir: Path) -> str:
    status = read_json(job_dir / "status.json")
    script = read_json(job_dir / "script.json")
    if not (job_dir / "APPROVED").exists():
        raise UploadError("Review gate: no APPROVED file in the job folder; not uploading.")
    if status.get("video_id"):
        raise UploadError(f"Already uploaded as {status['video_id']}")
    video = job_dir / "final.mp4"
    if not video.exists():
        raise UploadError("final.mp4 missing; run assemble first")

    when = publish_at(status)
    up = CONFIG["upload"]
    body_status = {
        "privacyStatus": up["privacy_until_publish"],
        "selfDeclaredMadeForKids": up["made_for_kids"],
        "containsSyntheticMedia": up["altered_content"],
    }
    if when - datetime.now(timezone.utc) >= MIN_SCHEDULE_LEAD:
        body_status["publishAt"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        log.warning("Slot %s has passed or is too close; uploading as private without a schedule. "
                    "Publish it manually in Studio.", when)
    body = {
        "snippet": {
            "title": script["title"][:100],
            "description": build_description(script, credits(job_dir, status)),
            "tags": trim_tags(script.get("tags", [])),
            "categoryId": up["category_id"],
            "defaultLanguage": CONFIG["channel"]["language"],
            "defaultAudioLanguage": CONFIG["channel"]["language"],
        },
        "status": body_status,
    }

    yt = youtube()
    request = yt.videos().insert(part="snippet,status", body=body,
                                 media_body=MediaFileUpload(str(video), chunksize=8 << 20, resumable=True))
    response, retries = None, 0
    while response is None:
        try:
            progress, response = request.next_chunk()
            if progress:
                log.info("Uploading %s: %d%%", job_dir.name, int(progress.progress() * 100))
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and retries < 5:
                retries += 1
                log.warning("Upload hiccup (%s); retrying chunk", e.resp.status)
                continue
            raise
    video_id = response["id"]

    thumb = next((p for p in (job_dir / "thumb.png", job_dir / "thumb.jpg") if p.exists()), None)
    if status["type"] == "long" and thumb:
        yt.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumb))).execute()

    try:  # the upload already succeeded: never let this step lose the video_id
        confirmed = ensure_metadata(yt, video_id, script)
    except HttpError as e:
        log.warning("Could not confirm AI-use/tags for %s (%s); check them in Studio", video_id, e)
        confirmed = {"disclosure": False, "tags": False}

    visuals = read_json(job_dir / "visuals.json", {"shots": []})
    status.update(status="scheduled" if "publishAt" in body_status else "uploaded_private",
                  video_id=video_id, uploaded_at=now().isoformat(timespec="seconds"),
                  publish_at_utc=body_status.get("publishAt"),
                  disclosure_confirmed=confirmed["disclosure"], tags_confirmed=confirmed["tags"])
    write_json(job_dir / "status.json", status)

    history = read_json(DATA_DIR / "history.json", [])
    history.append({
        "job_id": status["job_id"], "type": status["type"], "idea_id": status["idea_id"],
        "subtopic": status["subtopic"], "title": script["title"], "publish_date": status["publish_date"],
        "video_id": video_id,
        "clip_ids": sorted({s["id"] for s in visuals["shots"] if s["kind"] not in ("card", "motion")}),
        "thumb_layout": status.get("thumb_layout"),
        "caption_style": status.get("caption_style"), "music": status.get("music"),
        "uploaded_at": status["uploaded_at"],
    })
    write_json(DATA_DIR / "history.json", history)

    backlog = ideas.load()
    for idea in backlog:
        if idea["id"] == status["idea_id"]:
            idea["status"] = "published"
    ideas.save(backlog)

    log.info("Uploaded %s as https://youtu.be/%s (%s, publishAt %s)", job_dir.name, video_id,
             body_status["privacyStatus"], body_status.get("publishAt", "none"))
    return video_id


def ensure_metadata(yt, video_id: str, script: dict) -> dict:
    """Read the video back; re-apply the AI-use disclosure and tags if they didn't stick.

    Returns {"disclosure": True/False/None, "tags": bool}. In testing YouTube did
    not report containsSyntheticMedia at all (even when set in Studio), so None
    means "can't be checked via the API": confirm "AI use" in Studio.
    """
    def read():
        return yt.videos().list(part="snippet,status", id=video_id).execute()["items"][0]

    want = CONFIG["upload"]["altered_content"]
    tags = trim_tags(script.get("tags", []))
    video = read()
    reported = video["status"].get("containsSyntheticMedia")
    if reported is not None and reported != want:  # only act on an explicit wrong value
        status = {k: video["status"][k] for k in WRITABLE_STATUS if k in video["status"]}
        status["containsSyntheticMedia"] = want
        yt.videos().update(part="status", body={"id": video_id, "status": status}).execute()
    if tags and not video["snippet"].get("tags"):
        snippet = {k: video["snippet"][k] for k in ("title", "description", "categoryId",
                                                    "defaultLanguage", "defaultAudioLanguage")
                   if k in video["snippet"]}
        yt.videos().update(part="snippet", body={"id": video_id, "snippet": {**snippet, "tags": tags}}).execute()
    video = read()
    reported = video["status"].get("containsSyntheticMedia")
    result = {"disclosure": None if reported is None else reported == want,
              "tags": bool(video["snippet"].get("tags")) or not tags}
    if result["disclosure"] is None:
        log.info("Reminder: confirm 'AI use' = Yes in Studio for %s (the API doesn't report it)", video_id)
    elif not result["disclosure"]:
        log.warning("AI-use disclosure is off for %s: set 'AI use' = Yes in Studio", video_id)
    if not result["tags"]:
        log.warning("Tags not confirmed for %s: add them in Studio", video_id)
    return result


def verify(job_dir: Path) -> dict:
    status = read_json(job_dir / "status.json")
    items = youtube().videos().list(part="snippet,status,processingDetails",
                                    id=status["video_id"]).execute().get("items", [])
    if not items:
        raise UploadError(f"video {status['video_id']} not found on the channel")
    return items[0]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Upload a job's final.mp4 to YouTube")
    parser.add_argument("job_dir", type=Path, nargs="?")
    parser.add_argument("--login", action="store_true", help="one-time browser login")
    parser.add_argument("--verify", action="store_true", help="read the uploaded video back")
    parser.add_argument("--fix-metadata", action="store_true",
                        help="re-apply AI-use disclosure and tags on an uploaded video")
    args = parser.parse_args()
    if args.login:
        login()
    elif args.job_dir and args.fix_metadata:
        st = read_json(args.job_dir / "status.json")
        result = ensure_metadata(youtube(), st["video_id"], read_json(args.job_dir / "script.json"))
        st.update(disclosure_confirmed=result["disclosure"], tags_confirmed=result["tags"])
        write_json(args.job_dir / "status.json", st)
        disclosure = {True: "yes", False: "NO", None: "not reported by API (check Studio)"}[result["disclosure"]]
        print(f"AI-use disclosure: {disclosure}   tags confirmed: {result['tags']}")
    elif args.job_dir and args.verify:
        v = verify(args.job_dir)
        s, st = v["snippet"], v["status"]
        print(f"title:        {s['title']}")
        print(f"privacy:      {st['privacyStatus']}   publishAt: {st.get('publishAt')}")
        print(f"madeForKids:  {st.get('selfDeclaredMadeForKids', st.get('madeForKids'))}   "
              f"synthetic: {st.get('containsSyntheticMedia')}   upload: {st.get('uploadStatus')}")
        print(f"category:     {s.get('categoryId')}   tags: {len(s.get('tags', []))}")
        print("description:\n" + s["description"])
    elif args.job_dir:
        upload(args.job_dir)
    else:
        parser.error("give a job folder, or --login")
