"""
backend/storage/usage_tracker.py
SQLite-based API usage tracking with token counts, costs, and timing.
Uses aiosqlite for async I/O.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

import aiosqlite

from backend.config import Config, get_config
from backend.schemas.task import ModelUsage, ProviderName, UsageSummary

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS api_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider        TEXT    NOT NULL,
    model           TEXT    NOT NULL,
    prompt_tokens   INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL    NOT NULL DEFAULT 0.0,
    latency_ms      REAL    NOT NULL DEFAULT 0.0,
    task_id         TEXT,
    conversation_id TEXT,
    extra_json      TEXT,
    created_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_provider ON api_usage(provider);
CREATE INDEX IF NOT EXISTS idx_usage_created  ON api_usage(created_at);
CREATE INDEX IF NOT EXISTS idx_usage_task     ON api_usage(task_id);
"""


class UsageTracker:
    """Persists and queries API usage records in SQLite."""

    def __init__(self, config: Optional[Config] = None) -> None:
        self._cfg = config or get_config()
        self._db_path: Path = self._cfg.db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialised = False

    # ------------------------------------------------------------------
    # DB lifecycle
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _connect(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            yield db

    async def initialise(self) -> None:
        """Create tables if they don't exist."""
        if self._initialised:
            return
        async with self._connect() as db:
            await db.executescript(_CREATE_TABLE_SQL)
            await db.commit()
        self._initialised = True
        logger.info("UsageTracker initialised at %s", self._db_path)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def record(
        self,
        usage: ModelUsage,
        task_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> int:
        """Insert a usage record. Returns the new row ID."""
        await self.initialise()
        async with self._connect() as db:
            cursor = await db.execute(
                """
                INSERT INTO api_usage
                    (provider, model, prompt_tokens, completion_tokens, total_tokens,
                     cost_usd, latency_ms, task_id, conversation_id, extra_json, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    usage.provider.value,
                    usage.model,
                    usage.prompt_tokens,
                    usage.completion_tokens,
                    usage.total_tokens,
                    usage.estimated_cost_usd,
                    usage.latency_ms,
                    task_id,
                    conversation_id,
                    json.dumps(extra) if extra else None,
                    usage.timestamp.isoformat(),
                ),
            )
            await db.commit()
            return cursor.lastrowid  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    async def get_summary(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        provider: Optional[ProviderName] = None,
    ) -> UsageSummary:
        """Aggregate stats for the given time window and optional provider filter."""
        await self.initialise()
        clauses: list[str] = []
        params: list[Any] = []

        if since:
            clauses.append("created_at >= ?")
            params.append(since.isoformat())
        if until:
            clauses.append("created_at <= ?")
            params.append(until.isoformat())
        if provider:
            clauses.append("provider = ?")
            params.append(provider.value)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        async with self._connect() as db:
            # Overall totals
            row = await db.execute_fetchall(
                f"SELECT COUNT(*) as cnt, SUM(total_tokens) as tkn, SUM(cost_usd) as cost FROM api_usage {where}",
                params,
            )
            total_cnt = row[0]["cnt"] if row else 0
            total_tkn = row[0]["tkn"] or 0 if row else 0
            total_cost = row[0]["cost"] or 0.0 if row else 0.0

            # Per-provider breakdown
            by_prov_rows = await db.execute_fetchall(
                f"""
                SELECT provider, model,
                       COUNT(*) as requests,
                       SUM(prompt_tokens) as pt,
                       SUM(completion_tokens) as ct,
                       SUM(total_tokens) as tt,
                       SUM(cost_usd) as cost,
                       AVG(latency_ms) as avg_lat
                FROM api_usage {where}
                GROUP BY provider, model
                """,
                params,
            )

        by_provider: dict[str, dict[str, Any]] = {}
        for r in by_prov_rows:
            prov = r["provider"]
            if prov not in by_provider:
                by_provider[prov] = {
                    "requests": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                    "avg_latency_ms": 0.0,
                    "models": {},
                }
            by_provider[prov]["requests"] += r["requests"]
            by_provider[prov]["prompt_tokens"] += r["pt"] or 0
            by_provider[prov]["completion_tokens"] += r["ct"] or 0
            by_provider[prov]["total_tokens"] += r["tt"] or 0
            by_provider[prov]["cost_usd"] += r["cost"] or 0.0
            by_provider[prov]["avg_latency_ms"] = r["avg_lat"] or 0.0
            by_provider[prov]["models"][r["model"]] = {
                "requests": r["requests"],
                "total_tokens": r["tt"] or 0,
                "cost_usd": r["cost"] or 0.0,
            }

        return UsageSummary(
            total_requests=total_cnt,
            total_tokens=total_tkn,
            total_cost_usd=total_cost,
            by_provider=by_provider,
            period_start=since,
            period_end=until,
        )

    async def recent_records(
        self,
        limit: int = 50,
        provider: Optional[ProviderName] = None,
        task_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Return the most recent usage records."""
        await self.initialise()
        clauses: list[str] = []
        params: list[Any] = []
        if provider:
            clauses.append("provider = ?")
            params.append(provider.value)
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)

        async with self._connect() as db:
            rows = await db.execute_fetchall(
                f"SELECT * FROM api_usage {where} ORDER BY created_at DESC LIMIT ?",
                params,
            )
        return [dict(r) for r in rows]

    async def total_cost_today(self) -> float:
        """Convenience: total cost incurred today (UTC)."""
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        summary = await self.get_summary(since=today)
        return summary.total_cost_usd

    async def cleanup_old_records(self, keep_days: int = 30) -> int:
        """Delete records older than keep_days. Returns number of rows deleted."""
        await self.initialise()
        cutoff = datetime.utcnow()
        from datetime import timedelta
        cutoff -= timedelta(days=keep_days)
        async with self._connect() as db:
            cursor = await db.execute(
                "DELETE FROM api_usage WHERE created_at < ?",
                (cutoff.isoformat(),),
            )
            await db.commit()
            return cursor.rowcount
