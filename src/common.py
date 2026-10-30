"""Shared helpers: config, paths, JSON files, prompt filling, logging."""
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


CONFIG = load_config()
TZ = ZoneInfo(CONFIG["schedule"]["timezone"])
JOBS_DIR = ROOT / CONFIG["paths"]["jobs"]
DATA_DIR = ROOT / CONFIG["paths"]["data"]
LOGS_DIR = ROOT / CONFIG["paths"]["logs"]
PROMPTS_DIR = ROOT / CONFIG["paths"]["prompts"]
ASSETS_DIR = ROOT / CONFIG["paths"]["assets"]


def now() -> datetime:
    return datetime.now(TZ)


def read_json(path: Path, default=None):
    try:
        # utf-8-sig: tolerate a BOM (Notepad/PowerShell add one when the owner edits)
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path: Path, data) -> None:
    """Write via a temp file so a crash never leaves half-written JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def fill_prompt(name: str, **values) -> str:
    """Fill {placeholders} in prompts/<name>.txt.

    Only the given keys are replaced, so the literal JSON braces in the
    templates are left alone (str.format would choke on them).
    """
    text = (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def word_count(text: str) -> int:
    return len(re.findall(r"[\w'’-]+", text))


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "untitled"


def get_logger(name: str = "yt") -> logging.Logger:
    """One log file per day in logs/, plus console output."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    LOGS_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    file_handler = logging.FileHandler(LOGS_DIR / f"{now():%Y-%m-%d}.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(file_handler)
    logger.addHandler(console)
    logger.setLevel(logging.INFO)
    return logger
