"""Append one local JSONL record for each verified engineering run."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_WRITE_LOCK = threading.Lock()
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def telemetry_path() -> Path:
    configured = os.getenv("HYBRIDCODER_TELEMETRY_PATH")
    return Path(configured).expanduser() if configured else _PROJECT_ROOT / "logs" / "task_runs.jsonl"


def _append_line(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str) + "\n"
    with _WRITE_LOCK, path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(line)
        stream.flush()


async def append_task_run(payload: dict[str, Any]) -> None:
    """Persist telemetry without blocking the async request handler."""
    record = {"recorded_at": datetime.now(timezone.utc).isoformat(), **payload}
    try:
        await asyncio.to_thread(_append_line, telemetry_path(), record)
    except OSError:
        logger.exception("Could not append HybridCoder task telemetry")
