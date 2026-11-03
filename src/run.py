"""Orchestrator.

  python -m src.run generate --type short|long [--ensure]
      Picks an idea, writes script.json into a new job folder and marks it
      awaiting_review. The owner reviews it and creates an empty APPROVED file.
      --ensure writes one only when the next free slot is due tomorrow, so the
      scheduler can run it at every logon without queuing extra scripts.
  python -m src.run produce [--no-upload]
      For every APPROVED job not yet uploaded (earliest publish date first):
      voice -> chapters (long) -> captions -> visuals -> assemble -> thumbnail
      (long) -> upload. Steps whose output exists are skipped, so a re-run
      resumes. A failing step marks that job failed (with the step and error)
      and the run moves on; it is retried on later runs up to MAX_ATTEMPTS.
  python -m src.run stats
      Writes the weekly summary to logs/weekly_<year>-W<week>.md.
  python -m src.run tick
      generate --ensure for Shorts and long videos, then produce: what the
      watcher runs every 5 minutes and Task Scheduler every 30 as a backup.
  python -m src.run watch
      The watcher: stays running from sign-in and starts a tick every 5 minutes.

Every command takes a lock (logs/run.lock), so two runs never overlap.
"""
import argparse
import os
import subprocess
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from . import ideas, script
from .claude_cli import UsageLimitError
from .common import CONFIG, JOBS_DIR, LOGS_DIR, ROOT, get_logger, now, read_json, slugify, write_json
from .jobs import PaceError, history, list_jobs, next_slot

log = get_logger()

MAX_ATTEMPTS = 3
RETRY_WAIT = timedelta(minutes=30)     # between attempts at a failed job
GENERATE_WAIT = timedelta(minutes=60)  # after a failed script, so frequent ticks don't burn Claude usage
WATCH_SECONDS = 300
LOCK = LOGS_DIR / "run.lock"
WATCH_LOCK = LOGS_DIR / "watch.lock"
BACKOFF = LOGS_DIR / "generate_backoff.json"


class Busy(RuntimeError):
    pass


@contextmanager
def run_lock(path: Path = LOCK):
    """OS-level lock on a file in logs/; released automatically if the process dies."""
    LOGS_DIR.mkdir(exist_ok=True)
    fh = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise Busy("another run is in progress")
        else:
            import fcntl
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise Busy("another run is in progress")
        yield
    finally:
        fh.close()


# ---------- generate ----------

ENSURE_LEAD_DAYS = 1  # scripts are written the day before their slot


def generate(video_type: str, ensure: bool = False) -> int:
    """Write the next script. With ensure, only when the next free slot is due within
    ENSURE_LEAD_DAYS, so it is safe to run at every logon and several times a day:
    missed scheduled runs catch up and no run ever queues extra scripts."""
    try:
        publish_date = next_slot(video_type)
    except PaceError as e:
        log.debug("Not generating: %s", e)
        return 0
    if ensure and (publish_date - now().date()).days > ENSURE_LEAD_DAYS:
        log.debug("Ensure %s: next free slot is %s, nothing due yet", video_type, publish_date)
        return 0
    backoff = read_json(BACKOFF, {})
    if ensure and video_type in backoff and now() - datetime.fromisoformat(backoff[video_type]) < GENERATE_WAIT:
        log.debug("Ensure %s: waiting after a recent failure", video_type)
        return 0
    try:
        idea = ideas.pick(video_type, publish_date)
        log.info("Writing %s for %s: [%s] %s", video_type, publish_date, idea["subtopic"], idea["title_angle"])
        writer = script.write_short if video_type == "short" else script.write_long
        content = writer(idea["title_angle"])
    except UsageLimitError as e:
        log.warning("Claude Pro usage limit reached; will retry in an hour. (%s)", str(e)[:200])
        write_json(BACKOFF, {**backoff, video_type: now().isoformat(timespec="seconds")})
        return 0
    except Exception as e:  # noqa: BLE001 - log it clearly; nothing was written
        log.error("generate %s failed: %s: %s\n%s", video_type, type(e).__name__, e, traceback.format_exc())
        write_json(BACKOFF, {**backoff, video_type: now().isoformat(timespec="seconds")})
        return 1
    if video_type in backoff:
        del backoff[video_type]
        write_json(BACKOFF, backoff)

    job_id = f"{publish_date:%Y-%m-%d}_{video_type}_{slugify(content['title'])}"
    folder = JOBS_DIR / job_id
    folder.mkdir(parents=True)
    write_json(folder / "script.json", content)
    write_json(folder / "status.json", {
        "job_id": job_id,
        "type": video_type,
        "status": "awaiting_review",
        "idea_id": idea["id"],
        "subtopic": idea["subtopic"],
        "title_angle": idea["title_angle"],
        "publish_date": publish_date.isoformat(),
        "publish_time": CONFIG["schedule"][f"{video_type}_publish"],
        "timezone": CONFIG["schedule"]["timezone"],
        "created_at": now().isoformat(timespec="seconds"),
        "error": None,
    })
    ideas.mark_scripted(idea["id"], job_id)
    log.info("Job ready for review: %s (%d words, %d scenes). Create APPROVED in it after review.",
             folder, content["word_count"], len(content["scenes"]))
    return 0


# ---------- produce ----------

def _steps(job: Path, video_type: str, upload_enabled: bool):
    """(name, already_done, action) in order. Imports are lazy: torch/whisper are heavy."""
    def status():
        return read_json(job / "status.json")

    def chapters():
        script.update_chapters(job)
        st = status()
        st["chapters_from_voice"] = True
        write_json(job / "status.json", st)

    def thumb_done():
        return (job / "thumb.png").exists() or (job / "thumb.jpg").exists()

    def voice_step():
        from . import voice
        voice.run(job)

    def captions_step():
        from . import captions
        captions.run(job)

    def visuals_step():
        from . import visuals
        visuals.run(job)

    def assemble_step():
        from . import assemble
        assemble.run(job)

    def thumbnail_step():
        from . import thumbnail
        thumbnail.run(job)

    def upload_step():
        from . import upload
        upload.upload(job)

    steps = [
        ("voice", lambda: (job / "voice.json").exists(), voice_step),
        ("chapters", lambda: status().get("chapters_from_voice"), chapters),
        ("captions", lambda: (job / "captions.json").exists(), captions_step),
        ("visuals", lambda: (job / "visuals.json").exists(), visuals_step),
        ("assemble", lambda: (job / "final.mp4").exists(), assemble_step),
        ("thumbnail", thumb_done, thumbnail_step),
        ("upload", lambda: bool(status().get("video_id")), upload_step),
    ]
    if video_type == "short":
        steps = [s for s in steps if s[0] not in ("chapters", "thumbnail")]
    if not upload_enabled:
        steps = [s for s in steps if s[0] != "upload"]
    return steps


def _week_count(video_type: str, publish_date: str) -> int:
    from datetime import date
    week = date.fromisoformat(publish_date).isocalendar()[:2]
    return sum(1 for h in history() if h.get("type") == video_type and h.get("publish_date")
               and date.fromisoformat(h["publish_date"]).isocalendar()[:2] == week)


def produce(upload_enabled: bool = True) -> int:
    jobs = [j for j in list_jobs() if (j["path"] / "APPROVED").exists() and not j.get("video_id")]
    waiting = [j["job_id"] for j in list_jobs() if not (j["path"] / "APPROVED").exists()]
    if waiting:
        log.debug("Awaiting review (no APPROVED file): %s", ", ".join(waiting))
    if not jobs:
        log.debug("Nothing approved to produce")
        return 0
    jobs.sort(key=lambda j: (j["publish_date"], j["job_id"]))
    failures = 0
    for job in jobs:
        path, attempts = job["path"], job.get("attempts", 0)
        if job.get("status") == "failed":
            if attempts >= MAX_ATTEMPTS:  # the weekly summary lists it, with its error
                log.debug("Skipping %s: failed %d times (last: %s). Fix it, then delete \"attempts\" "
                          "from status.json to retry.", job["job_id"], attempts, job.get("error"))
                continue
            if job.get("failed_at") and now() - datetime.fromisoformat(job["failed_at"]) < RETRY_WAIT:
                log.debug("Skipping %s: waiting before retry %d", job["job_id"], attempts + 1)
                continue
        cap_key = "max_shorts_per_week" if job["type"] == "short" else "max_longs_per_week"
        if upload_enabled and _week_count(job["type"], job["publish_date"]) >= CONFIG["pace"][cap_key]:
            log.debug("Skipping %s: the pace cap for that week is already reached", job["job_id"])
            continue
        log.info("Producing %s (publish %s)", job["job_id"], job["publish_date"])
        for name, done, action in _steps(path, job["type"], upload_enabled):
            if done():
                continue
            st = read_json(path / "status.json")
            st.update(status=f"producing:{name}")
            write_json(path / "status.json", st)
            try:
                action()
            except UsageLimitError as e:
                log.warning("Claude Pro usage limit reached at %s/%s; stopping this run, will resume later. (%s)",
                            job["job_id"], name, str(e)[:200])
                st = read_json(path / "status.json")
                st.update(status="approved")  # paused, not failed
                write_json(path / "status.json", st)
                return 0
            except Exception as e:  # noqa: BLE001 - one job's failure must not block the others
                st = read_json(path / "status.json")
                st.update(status="failed", error=f"{name}: {type(e).__name__}: {e}"[:500],
                          attempts=st.get("attempts", 0) + 1, failed_at=now().isoformat(timespec="seconds"))
                write_json(path / "status.json", st)
                log.error("FAILED %s at step %s (attempt %d/%d): %s: %s\n%s", job["job_id"], name,
                          st["attempts"], MAX_ATTEMPTS, type(e).__name__, e, traceback.format_exc())
                failures += 1
                break
        else:  # every step done (upload sets its own scheduled/uploaded_private status)
            st = read_json(path / "status.json")
            if not upload_enabled:
                st["status"] = "produced"
            st["error"] = None
            write_json(path / "status.json", st)
            log.info("Done: %s (%s)", job["job_id"], st["status"])
    return 1 if failures else 0


def tick() -> int:
    """The scheduler's one recurring job: write any script due tomorrow, then make and upload
    whatever is approved. Cheap when nothing is due (no Claude call, no rendering), so it runs
    every few minutes; one process and one lock means the steps never trip over each other."""
    codes = [generate("short", ensure=True), generate("long", ensure=True), produce()]
    return max(codes)


def _log_line(message: str) -> None:
    """Append to today's log. The watcher outlives any one day's file handler, so it writes directly."""
    LOGS_DIR.mkdir(exist_ok=True)
    with open(LOGS_DIR / f"{now():%Y-%m-%d}.log", "a", encoding="utf-8") as f:
        f.write(f"{now():%Y-%m-%d %H:%M:%S} INFO {message}\n")


def watch() -> int:
    """Stay running and start a tick every WATCH_SECONDS, independent of Task Scheduler's
    repeating timer (which skipped runs without explanation on 2 Nov 2026). Each tick is a
    child process, so the heavy libraries are never held in memory between checks. A
    long render simply delays the next check. Logs a heartbeat once an hour."""
    try:
        with run_lock(WATCH_LOCK):
            _log_line(f"Watcher started (pid {os.getpid()}), checking every {WATCH_SECONDS // 60} minutes")
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            checks, last_beat = 0, time.monotonic()
            while True:
                try:
                    subprocess.run([sys.executable, "-m", "src.run", "tick"], cwd=ROOT, creationflags=flags)
                    checks += 1
                except Exception as e:  # noqa: BLE001 - the watcher must outlive any one tick
                    _log_line(f"Watcher could not start a tick: {type(e).__name__}: {e}")
                if time.monotonic() - last_beat >= 3600:
                    _log_line(f"Watcher alive: {checks} checks in the last hour")
                    checks, last_beat = 0, time.monotonic()
                time.sleep(WATCH_SECONDS)
    except Busy:
        return 0  # already running: normal when sign-in and unlock both start us


# ---------- main ----------

def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m src.run")
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="write the next script for review")
    gen.add_argument("--type", choices=["short", "long"], required=True)
    gen.add_argument("--ensure", action="store_true",
                     help="only write a script if the next slot (due tomorrow) has none; safe to run often")
    prod = sub.add_parser("produce", help="produce and upload every approved job")
    prod.add_argument("--no-upload", action="store_true", help="stop before uploading (testing)")
    sub.add_parser("stats", help="write the weekly summary")
    sub.add_parser("tick", help="generate --ensure for both types, then produce (what the scheduler runs)")
    sub.add_parser("watch", help="stay running and start a tick every 5 minutes")
    args = parser.parse_args()
    if args.command == "watch":
        return watch()
    try:
        with run_lock():
            # a tick usually has nothing to do; only the work it does gets logged
            (log.debug if args.command == "tick" else log.info)("=== run %s ===", " ".join(sys.argv[1:]))
            if args.command == "generate":
                return generate(args.type, ensure=args.ensure)
            if args.command == "produce":
                return produce(upload_enabled=not args.no_upload)
            if args.command == "tick":
                return tick()
            if args.command == "stats":
                from .stats import weekly_summary
                path = weekly_summary()
                log.info("Weekly summary: %s", path)
                return 0
    except Busy as e:
        (log.debug if args.command == "tick" else log.info)("Not starting: %s", e)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
