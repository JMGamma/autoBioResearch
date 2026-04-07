"""
Shared FastAPI dependencies: DB engine singleton and per-request connection.
"""
from __future__ import annotations

import threading
from typing import Generator

from sqlalchemy import event, create_engine
from sqlalchemy.engine import Connection, Engine

from autobioresearch.config import AppConfig

_engine: Engine | None = None
_engine_lock = threading.Lock()


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:  # double-check inside lock
                cfg = AppConfig.from_yaml()
                engine = create_engine(
                    f"sqlite:///{cfg.db_path}",
                    connect_args={"check_same_thread": False},
                )

                @event.listens_for(engine, "connect")
                def _set_pragma(dbapi_conn, _record):
                    cur = dbapi_conn.cursor()
                    # Retry up to 30 s on lock contention instead of failing instantly.
                    cur.execute("PRAGMA busy_timeout=30000")
                    # WAL persists on disk but must be confirmed per-connection.
                    cur.execute("PRAGMA journal_mode=WAL")
                    cur.execute("PRAGMA synchronous=NORMAL")
                    # Up to 256 MiB page cache (65536 pages × 4 KiB) for read-heavy API.
                    cur.execute("PRAGMA cache_size=-65536")
                    cur.execute("PRAGMA temp_store=MEMORY")
                    cur.close()

                _engine = engine
    return _engine


def get_conn() -> Generator[Connection, None, None]:
    with get_engine().connect() as conn:
        yield conn
