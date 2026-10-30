"""Weekly summary: logs/weekly_<year>-W<week>.md

Uploads this week with live YouTube numbers, things needing a human (review
queue, failed jobs, AI-use checks in Studio, videos stuck private), schedule
gaps for the coming week, backlog size and login age.
"""
from datetime import datetime, timedelta

from .common import CONFIG, LOGS_DIR, ROOT, get_logger, now, read_json
from .ideas import load as load_ideas
from .jobs import WEEKDAYS, history, list_jobs

log = get_logger()
TOKEN_WARN_DAYS = 5  # Testing-mode refresh tokens expire after ~7 days


def _youtube_numbers(video_ids: list[str]) -> tuple[dict, str | None]:
    if not video_ids:
        return {}, None
    try:
        from .upload import youtube
        yt = youtube()
        out = {}
        for i in range(0, len(video_ids), 50):
            resp = yt.videos().list(part="statistics,status", id=",".join(video_ids[i:i + 50])).execute()
            for item in resp.get("items", []):
                out[item["id"]] = {**item.get("statistics", {}), "privacy": item["status"]["privacyStatus"]}
        return out, None
    except Exception as e:  # noqa: BLE001 - the summary must still be written
        return {}, f"{type(e).__name__}: {e}"


def _failed_log_lines(days: int = 7) -> list[str]:
    lines = []
    for d in range(days):
        path = LOGS_DIR / f"{now().date() - timedelta(days=d):%Y-%m-%d}.log"
        if path.exists():
            lines += [l.strip() for l in path.read_text(encoding="utf-8", errors="replace").splitlines()
                      if " ERROR " in l and "FAILED" in l]
    return lines


def weekly_summary() -> str:
    today = now().date()
    year, week, _ = today.isocalendar()
    since = now() - timedelta(days=7)
    hist = history()
    jobs = list_jobs()
    status_by_job = {j["job_id"]: j for j in jobs}

    this_week = [h for h in hist if h.get("uploaded_at") and datetime.fromisoformat(h["uploaded_at"]) >= since]
    recent = [h for h in hist if h.get("uploaded_at")
              and datetime.fromisoformat(h["uploaded_at"]) >= now() - timedelta(days=28)]
    numbers, yt_error = _youtube_numbers([h["video_id"] for h in recent])

    out = [f"# Weekly summary {year}-W{week:02d}", f"Generated {now():%Y-%m-%d %H:%M} ({CONFIG['schedule']['timezone']})", ""]

    out += ["## Uploaded in the last 7 days", ""]
    if this_week:
        out += ["| Publish | Type | Title | Views | Likes | Comments | Privacy now |", "|---|---|---|---|---|---|---|"]
        for h in this_week:
            n = numbers.get(h["video_id"], {})
            out.append(f"| {h['publish_date']} | {h['type']} | [{h['title']}](https://youtu.be/{h['video_id']}) | "
                       f"{n.get('viewCount', '?')} | {n.get('likeCount', '?')} | {n.get('commentCount', '?')} | "
                       f"{n.get('privacy', '?')} |")
    else:
        out.append("Nothing uploaded.")
    if yt_error:
        out += ["", f"YouTube numbers unavailable: {yt_error}"]

    older = [h for h in recent if h not in this_week]
    if older:
        out += ["", "### Earlier uploads (last 28 days)", ""]
        out += [f"- {h['publish_date']} {h['type']}: {h['title']} — "
                f"{numbers.get(h['video_id'], {}).get('viewCount', '?')} views" for h in older]

    todo = []
    for h in this_week:
        st = status_by_job.get(h["job_id"], {})
        if st.get("disclosure_confirmed") is not True:
            todo.append(f"Confirm **AI use = Yes** in Studio for \"{h['title']}\" (https://youtu.be/{h['video_id']})")
        publish = datetime.fromisoformat(f"{h['publish_date']}T{CONFIG['schedule'][h['type'] + '_publish']}")
        if numbers.get(h["video_id"], {}).get("privacy") == "private" and publish.date() < today:
            todo.append(f"\"{h['title']}\" is still private after its publish date: the API project is probably "
                        "not audited yet. Publish it in Studio.")
    for j in jobs:
        if not (j["path"] / "APPROVED").exists():
            todo.append(f"Review script for {j['publish_date']} {j['type']}: `{j['job_id']}`")
        elif j.get("status") == "failed":
            todo.append(f"Failed job `{j['job_id']}` (attempt {j.get('attempts', 1)}): {j.get('error')}")
    token = ROOT / "token.json"
    if token.exists():
        age = (datetime.now() - datetime.fromtimestamp(token.stat().st_mtime)).days
        if age >= TOKEN_WARN_DAYS:
            todo.append(f"YouTube login is {age} days old; in Testing mode it expires after ~7. "
                        "Run `python -m src.upload --login`.")
    else:
        todo.append("No YouTube login: run `python -m src.upload --login`.")
    out += ["", "## Needs you", ""] + ([f"- [ ] {t}" for t in todo] or ["Nothing."])

    taken = {(j["type"], j["publish_date"]) for j in jobs} | {(h["type"], h["publish_date"]) for h in hist}
    long_days = {WEEKDAYS.index(d) for d in CONFIG["schedule"]["long_days"]}
    gaps = []
    for offset in range(1, 8):
        d = today + timedelta(days=offset)
        if ("short", d.isoformat()) not in taken:
            gaps.append(f"{d:%a %d %b} short")
        if d.weekday() in long_days and ("long", d.isoformat()) not in taken:
            gaps.append(f"{d:%a %d %b} long")
    out += ["", "## Next 7 days", "", "Open slots (scheduled tasks will fill these): " + (", ".join(gaps) or "none")]

    backlog = [i for i in load_ideas() if i["status"] == "new"]
    out += ["", "## Backlog", "",
            f"{sum(1 for i in backlog if i['type'] == 'short')} short and "
            f"{sum(1 for i in backlog if i['type'] == 'long')} long ideas unused."]

    failed = _failed_log_lines()
    out += ["", "## Failures logged this week", ""] + ([f"- `{l}`" for l in failed[-15:]] or ["None."])

    path = LOGS_DIR / f"weekly_{year}-W{week:02d}.md"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return str(path)
