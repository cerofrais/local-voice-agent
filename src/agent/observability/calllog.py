"""Local call logs: one JSON per call under data/calls/, pruned after a retention period."""

import json
import time
from pathlib import Path
from typing import Any

from loguru import logger

RETENTION_DAYS = 30  # docs/07 section 7


def write_call_log(directory: Path, call_id: str, payload: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{call_id}.json"
    path.write_text(json.dumps(payload, indent=2, default=str, ensure_ascii=False))
    return path


def prune_old_logs(directory: Path, days: int = RETENTION_DAYS) -> int:
    if not directory.exists():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for f in directory.glob("*.json"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            removed += 1
    if removed:
        logger.info(f"pruned {removed} call log(s) older than {days} days")
    return removed
