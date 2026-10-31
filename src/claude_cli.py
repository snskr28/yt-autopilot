"""Wrapper around Claude Code in headless mode (`claude -p`).

All AI text goes through the Claude Pro login. Every ANTHROPIC_* variable (API
key, auth token, base URL) is stripped from the child environment, so nothing is
billed as API usage or routed through a third-party relay.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .common import get_logger

log = get_logger()

TIMEOUT_SECONDS = 600
LIMIT_PATTERNS = re.compile(
    r"usage limit|limit reached|rate limit|limit will reset|resets at|out of (extra )?usage",
    re.IGNORECASE,
)
# right after the PC wakes, the network can take a minute to come back
NETWORK_PATTERNS = re.compile(
    r"ECONNREFUSED|ECONNRESET|ETIMEDOUT|ENOTFOUND|EAI_AGAIN|connection refused|network|socket hang up",
    re.IGNORECASE,
)
NETWORK_RETRIES, NETWORK_WAIT = 3, 30


class ClaudeError(RuntimeError):
    pass


class UsageLimitError(ClaudeError):
    """Pro usage window is exhausted; the scheduler should simply retry later."""


def _clean_env() -> dict:
    """The child environment without anything that would reroute or rebill claude -p.

    Any ANTHROPIC_* variable (API key, auth token, base URL) would make Claude Code use an API
    key or a third-party relay instead of the Claude Pro login saved in ~/.claude; the Claude
    desktop app's own session variables would tie the call to an app session. Strip them all,
    so `claude -p` always runs on the saved Pro login.
    """
    drop = ("ANTHROPIC_", "CLAUDE_CODE_", "CLAUDECODE", "CLAUDE_AGENT_SDK", "CLAUDE_PID", "CLAUDE_PREVIEW")
    return {k: v for k, v in os.environ.items() if not k.upper().startswith(drop)}


def _run(prompt: str) -> str:
    """One claude -p call, retrying transient network failures (e.g. just after wake)."""
    for attempt in range(NETWORK_RETRIES + 1):
        try:
            return _run_once(prompt)
        except ClaudeError as e:
            if isinstance(e, UsageLimitError) or not NETWORK_PATTERNS.search(str(e)) \
                    or attempt == NETWORK_RETRIES:
                raise
            log.warning("claude -p network error (%s); retrying in %ds", str(e)[:120], NETWORK_WAIT)
            time.sleep(NETWORK_WAIT)


def _run_once(prompt: str) -> str:
    # Task Scheduler may start us with a thinner PATH; fall back to the default install location
    exe = shutil.which("claude") or next(
        (str(p) for p in (Path.home() / ".local" / "bin" / "claude.exe", Path.home() / ".local" / "bin" / "claude")
         if p.exists()), None)
    if not exe:
        raise ClaudeError("claude is not on PATH")
    env = _clean_env()
    # Pure text generation: no tools, no MCP servers, no saved session, and a
    # neutral working directory so no project CLAUDE.md is pulled in.
    cmd = [exe, "-p", "--output-format", "json", "--tools", "",
           "--no-session-persistence", "--strict-mcp-config"]
    proc = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
        timeout=TIMEOUT_SECONDS, env=env, cwd=tempfile.gettempdir(),
    )
    raw = (proc.stdout or "").strip()
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        envelope = None

    if envelope is None or proc.returncode != 0 or envelope.get("is_error"):
        message = (envelope or {}).get("result") or proc.stderr.strip() or raw or "no output"
        if LIMIT_PATTERNS.search(message):
            raise UsageLimitError(message)
        raise ClaudeError(f"claude -p failed (exit {proc.returncode}): {message[:500]}")
    return envelope.get("result", "")


def _parse(text: str):
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if starts:
        text = text[min(starts):]
    # raw_decode ignores trailing chatter after the JSON value
    value, _ = json.JSONDecoder().raw_decode(text)
    return value


def ask(prompt: str, schema_hint: str = ""):
    """Send a prompt, return the parsed JSON (dict or list).

    Retries once with a "return only valid JSON" follow-up if parsing fails.
    """
    text = _run(prompt)
    try:
        return _parse(text)
    except (json.JSONDecodeError, ValueError):
        log.warning("Claude returned invalid JSON; retrying once")
    retry = (
        f"{prompt}\n\nYour previous reply was not valid JSON. Return only valid JSON, "
        f"no prose and no code fences.{(' Shape: ' + schema_hint) if schema_hint else ''}"
    )
    text = _run(retry)
    try:
        return _parse(text)
    except (json.JSONDecodeError, ValueError) as e:
        raise ClaudeError(f"invalid JSON after retry: {e}; reply began: {text[:200]!r}")


if __name__ == "__main__":
    prompt = " ".join(sys.argv[1:]) or 'Return only JSON: {"ok": true}'
    print(json.dumps(ask(prompt), indent=2, ensure_ascii=False))
