"""Idea backlog in data/ideas.json.

Idea: {id, type, subtopic, title_angle, why_interesting, status, created_at, job_id}
status: new -> scripted (job created) -> published (after upload).
A rejected script (job folder deleted) puts its idea back to new.
"""
import argparse
from datetime import date

from .claude_cli import ask
from .common import CONFIG, DATA_DIR, JOBS_DIR, fill_prompt, get_logger, now, read_json, write_json
from .jobs import history, scheduled_items

log = get_logger()
IDEAS_PATH = DATA_DIR / "ideas.json"
REFILL_BELOW = 10
RECENT_TITLES = 60


class NoIdeaError(RuntimeError):
    pass


def load() -> list[dict]:
    return read_json(IDEAS_PATH, [])


def save(ideas: list[dict]) -> None:
    write_json(IDEAS_PATH, ideas)


def _norm(subtopic: str | None) -> str:
    return (subtopic or "").strip().lower()


def reconcile(ideas: list[dict]) -> list[dict]:
    """Return ideas whose job folder was deleted (rejected) to the backlog."""
    published = {h.get("idea_id") for h in history()}
    for idea in ideas:
        if idea["status"] == "scripted" and idea["id"] not in published:
            if not idea.get("job_id") or not (JOBS_DIR / idea["job_id"]).exists():
                log.info("Idea %s returned to backlog (job %s removed)", idea["id"], idea.get("job_id"))
                idea["status"], idea["job_id"] = "new", None
    return ideas


def blocked_subtopics() -> list[str]:
    """Subtopics queued or published within the spacing window of today."""
    spacing = CONFIG["variety"]["min_days_between_same_subtopic"]
    today = now().date()
    return sorted({
        _norm(i["subtopic"]) for i in scheduled_items()
        if i.get("subtopic") and i.get("publish_date")
        and (date.fromisoformat(i["publish_date"]) - today).days > -spacing
    })


def relabel(ideas: list[dict]) -> list[dict]:
    """One-off: give unused ideas narrow subtopics (older backlogs used broad fields)."""
    targets = [i for i in ideas if i["status"] in ("new", "scripted")]
    if not targets:
        return ideas
    listing = "\n".join(f'{i["id"]}: {i["title_angle"]}' for i in targets)
    prompt = (
        "For each video idea below, give a narrow subtopic: one specific object, creature, "
        'phenomenon or body part (for example "Venus", "octopus", "lightning"), never a broad '
        f"field like \"astronomy\".\n{listing}\n"
        'Return only JSON: [{"id": int, "subtopic": str}]'
    )
    labels = {item["id"]: item["subtopic"] for item in ask(prompt, '[{"id", "subtopic"}]')}
    for idea in targets:
        if labels.get(idea["id"]):
            idea["subtopic"] = labels[idea["id"]]
            # keep the queued job's copy in step so spacing checks see the new label
            status_path = JOBS_DIR / (idea.get("job_id") or "") / "status.json"
            if idea.get("job_id") and status_path.exists():
                status = read_json(status_path)
                status["subtopic"] = idea["subtopic"]
                write_json(status_path, status)
    return ideas


def refill(ideas: list[dict], force: bool = False) -> list[dict]:
    """Ask Claude for 20 more ideas when fewer than 10 are unused."""
    fresh = [i for i in ideas if i["status"] == "new"]
    if len(fresh) >= REFILL_BELOW and not force:
        return ideas
    titles = [h["title"] for h in history() if h.get("title")][-RECENT_TITLES:]
    titles += [i["title_angle"] for i in ideas]  # also avoid duplicating the backlog
    blocked = blocked_subtopics()
    prompt = fill_prompt(
        "ideas", niche=CONFIG["channel"]["niche"], tone=CONFIG["channel"]["tone"],
        recent_titles="; ".join(titles) if titles else "(none yet)",
        blocked_subtopics=", ".join(blocked) if blocked else "(none)",
    )
    log.info("Backlog has %d unused ideas; asking Claude for 20 more", len(fresh))
    new = ask(prompt, '[{"type", "subtopic", "title_angle", "why_interesting"}]')
    if isinstance(new, dict):  # tolerate {"ideas": [...]}
        new = next((v for v in new.values() if isinstance(v, list)), [])
    next_id = max((i["id"] for i in ideas), default=0) + 1
    added = 0
    for item in new:
        if item.get("type") not in ("short", "long") or not item.get("title_angle"):
            continue
        ideas.append({
            "id": next_id, "type": item["type"], "subtopic": item.get("subtopic", ""),
            "title_angle": item["title_angle"], "why_interesting": item.get("why_interesting", ""),
            "status": "new", "created_at": now().isoformat(timespec="seconds"), "job_id": None,
        })
        next_id += 1
        added += 1
    log.info("Added %d ideas to the backlog", added)
    save(ideas)
    return ideas


def _eligible(idea: dict, video_type: str, publish_date: date, others: list[dict]) -> bool:
    if idea["status"] != "new" or idea["type"] != video_type:
        return False
    spacing = CONFIG["variety"]["min_days_between_same_subtopic"]
    for other in others:
        if other.get("publish_date") and _norm(other.get("subtopic")) == _norm(idea["subtopic"]):
            gap = abs((date.fromisoformat(other["publish_date"]) - publish_date).days)
            if gap < spacing:
                return False
    return True


def pick(video_type: str, publish_date: date) -> dict:
    """Next unused idea of this type whose subtopic is not too recent."""
    ideas = refill(reconcile(load()))
    save(ideas)
    others = scheduled_items()
    for attempt in range(2):
        for idea in ideas:
            if _eligible(idea, video_type, publish_date, others):
                return idea
        if attempt == 0:
            log.info("No eligible %s idea (subtopic spacing); refilling backlog", video_type)
            ideas = refill(ideas, force=True)
    raise NoIdeaError(
        f"No {video_type} idea respects the {CONFIG['variety']['min_days_between_same_subtopic']}-day "
        "subtopic spacing. Add ideas with other subtopics to data/ideas.json."
    )


def mark_scripted(idea_id: int, job_id: str) -> None:
    ideas = load()
    for idea in ideas:
        if idea["id"] == idea_id:
            idea["status"], idea["job_id"] = "scripted", job_id
    save(ideas)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Show or refill the idea backlog")
    parser.add_argument("--refill", action="store_true", help="ask Claude for 20 more now")
    parser.add_argument("--relabel", action="store_true", help="give unused ideas narrow subtopics")
    args = parser.parse_args()
    ideas = reconcile(load())
    if args.relabel:
        ideas = relabel(ideas)
    if args.refill:
        ideas = refill(ideas, force=True)
    save(ideas)
    for i in ideas:
        print(f"{i['id']:>3} {i['status']:<9} {i['type']:<5} [{i['subtopic']}] {i['title_angle']}")
