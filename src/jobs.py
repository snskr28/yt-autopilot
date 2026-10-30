"""Job folders, publish slots and the weekly pace cap."""
from datetime import date, timedelta

from .common import CONFIG, DATA_DIR, JOBS_DIR, now, read_json

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class PaceError(RuntimeError):
    """Queuing another job would break the weekly pace cap or the queue horizon."""


def list_jobs() -> list[dict]:
    """Every job folder that has a status.json, oldest first."""
    jobs = []
    if JOBS_DIR.exists():
        for folder in sorted(p for p in JOBS_DIR.iterdir() if p.is_dir()):
            status = read_json(folder / "status.json")
            if status:
                jobs.append({**status, "path": folder})
    return jobs


def history() -> list[dict]:
    return read_json(DATA_DIR / "history.json", [])


def scheduled_items() -> list[dict]:
    """Everything queued or published: {type, publish_date, subtopic, idea_id}."""
    items = [
        {k: j.get(k) for k in ("type", "publish_date", "subtopic", "idea_id", "job_id")}
        for j in list_jobs()
    ]
    queued = {i["job_id"] for i in items}
    items += [
        {k: h.get(k) for k in ("type", "publish_date", "subtopic", "idea_id", "job_id")}
        for h in history() if h.get("job_id") not in queued
    ]
    return items


def _week(d: date) -> tuple[int, int]:
    return d.isocalendar()[:2]


def next_slot(video_type: str) -> date:
    """First free publish date for this type, honouring the weekly cap.

    Shorts: one per day. Long videos: only on schedule.long_days. Refuses to
    queue further ahead than pace.max_queue_days_ahead (no bulk backlog).
    """
    pace = CONFIG["pace"]
    cap = pace["max_shorts_per_week"] if video_type == "short" else pace["max_longs_per_week"]
    horizon = pace["max_queue_days_ahead"]
    long_days = {WEEKDAYS.index(d) for d in CONFIG["schedule"]["long_days"]}

    taken = [date.fromisoformat(i["publish_date"]) for i in scheduled_items()
             if i["type"] == video_type and i.get("publish_date")]
    today = now().date()
    for offset in range(1, horizon + 1):
        day = today + timedelta(days=offset)
        if video_type == "long" and day.weekday() not in long_days:
            continue
        if day in taken:
            continue
        if sum(1 for t in taken if _week(t) == _week(day)) >= cap:
            continue
        return day
    raise PaceError(
        f"No free {video_type} slot in the next {horizon} days "
        f"(cap {cap}/week). Not queuing more; produce or publish what is queued first."
    )
