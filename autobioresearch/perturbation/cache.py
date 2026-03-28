"""
Perturbation result cache backed by a dedicated SQLite file (explorer_cache.db).

Separate from the research DB so the API can write cache entries without
competing with the crawler's writes to autobioresearch.db.

Cache semantics:
  - One row per (entity_id, mode) pair — most recent computation wins.
  - Each row stores the result at the deepest depth computed (max_depth).
  - On a cache hit:
      requested_depth <= max_depth  →  TRUNCATE  (filter affected list in memory, O(n))
      requested_depth >  max_depth  →  MISS       (treat as expired, recompute)
  - TTL: 24 hours from the time of computation.
  - Cache writes are intended to run in a FastAPI BackgroundTask so they never
    block the request that caused the compute.

ASCII flow:

  request(entity, mode, depth)
      │
      ▼
  lookup(entity_id, mode)
      ├── MISS or EXPIRED ──────────────────────────────▶ compute_fn()
      │                                                        │
      │                                               background_task: write()
      │                                                        │
      └── HIT ──▶ depth <= max_depth? ──YES──▶ truncate ◀────┘
                        │
                       NO
                        │
                        ▼
                   (treat as miss, compute again)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

logger = logging.getLogger("autobioresearch.cache")

_TTL_HOURS = 24
_CREATE_CACHE_TABLE = """
CREATE TABLE IF NOT EXISTS perturbation_cache (
    id          TEXT PRIMARY KEY,
    entity_id   TEXT NOT NULL,
    mode        TEXT NOT NULL,
    max_depth   INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    hit_count   INTEGER DEFAULT 0,
    UNIQUE(entity_id, mode)
);
"""
_CREATE_QUERY_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS query_log (
    id           TEXT PRIMARY KEY,
    entity_name  TEXT,
    mode         TEXT,
    depth        INTEGER,
    duration_ms  INTEGER,
    result_count INTEGER,
    created_at   TEXT
);
"""


class PerturbationCache:
    """
    Thread-safe perturbation result cache using a dedicated SQLite file.

    Uses WAL mode so concurrent readers (future use) don't block writes.
    """

    def __init__(self, db_path: str) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute(_CREATE_CACHE_TABLE)
        self._conn.execute(_CREATE_QUERY_LOG_TABLE)
        self._conn.commit()
        logger.info("PerturbationCache initialised at %s", path)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_or_compute(
        self,
        entity_id: str,
        mode: str,
        depth: int,
        compute_fn: Callable[[], dict],
    ) -> tuple[dict, bool]:
        """
        Return (result_dict, cache_hit).

        cache_hit=True  → caller should NOT queue a background write (already cached).
        cache_hit=False → caller should queue background_tasks.add_task(cache.write, ...).
        """
        row = self._lookup(entity_id, mode)
        now = datetime.now(timezone.utc)

        if row and datetime.fromisoformat(row["expires_at"]) > now:
            cached_depth: int = row["max_depth"]
            if depth <= cached_depth:
                result = self._truncate(json.loads(row["result_json"]), depth)
                self._increment_hit(entity_id, mode)
                logger.debug(
                    "cache HIT (truncate) entity=%s mode=%s depth=%d cached_depth=%d",
                    entity_id, mode, depth, cached_depth,
                )
                return result, True

        # Miss or expired or depth > cached_depth — full recompute.
        logger.debug("cache MISS entity=%s mode=%s depth=%d", entity_id, mode, depth)
        result = compute_fn()
        return result, False

    def write(self, entity_id: str, mode: str, depth: int, result: dict) -> None:
        """
        Upsert a cache row. Intended to run in a BackgroundTask.

        If a row already exists for (entity_id, mode) and the new depth is
        deeper, it replaces the row. If shallower, it still replaces (the
        fresh result is always correct — staleness was the issue, not depth).
        """
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=_TTL_HOURS)
        try:
            self._conn.execute(
                """
                INSERT INTO perturbation_cache
                    (id, entity_id, mode, max_depth, result_json, created_at, expires_at, hit_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(entity_id, mode) DO UPDATE SET
                    max_depth   = excluded.max_depth,
                    result_json = excluded.result_json,
                    created_at  = excluded.created_at,
                    expires_at  = excluded.expires_at,
                    hit_count   = 0
                """,
                (
                    str(uuid.uuid4()),
                    entity_id,
                    mode,
                    depth,
                    json.dumps(result),
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
            self._conn.commit()
            logger.debug("cache WRITE entity=%s mode=%s depth=%d", entity_id, mode, depth)
        except Exception:
            logger.exception("cache write failed for entity=%s mode=%s", entity_id, mode)

    def log_query(
        self,
        entity_name: str,
        mode: str,
        depth: int,
        duration_ms: int,
        result_count: int,
    ) -> None:
        """Append a query record to the query_log table (best-effort)."""
        try:
            self._conn.execute(
                "INSERT INTO query_log (id, entity_name, mode, depth, duration_ms, result_count, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    entity_name,
                    mode,
                    depth,
                    duration_ms,
                    result_count,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self._conn.commit()
        except Exception:
            logger.debug("query_log write failed (non-fatal)")

    # ── Private helpers ───────────────────────────────────────────────────────

    def _lookup(self, entity_id: str, mode: str) -> dict | None:
        cur = self._conn.execute(
            "SELECT max_depth, result_json, expires_at FROM perturbation_cache "
            "WHERE entity_id = ? AND mode = ?",
            (entity_id, mode),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {"max_depth": row[0], "result_json": row[1], "expires_at": row[2]}

    def _increment_hit(self, entity_id: str, mode: str) -> None:
        try:
            self._conn.execute(
                "UPDATE perturbation_cache SET hit_count = hit_count + 1 "
                "WHERE entity_id = ? AND mode = ?",
                (entity_id, mode),
            )
            self._conn.commit()
        except Exception:
            pass  # non-fatal

    @staticmethod
    def _truncate(result: dict, depth: int) -> dict:
        """
        Return a copy of result with affected list filtered to hop_depth <= depth.
        excluded_edges is not filtered (depth-independent).
        """
        filtered = [a for a in result.get("affected", []) if a.get("hop_depth", 0) <= depth]
        truncated = dict(result)
        truncated["affected"] = filtered
        truncated["stats"] = dict(result.get("stats", {}))
        truncated["stats"]["n_affected"] = len(filtered)
        return truncated
