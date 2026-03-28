"""
Shared FastAPI dependencies: DB engine singleton and per-request connection.
"""
from __future__ import annotations

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine

from autobioresearch.config import AppConfig

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        cfg = AppConfig.from_yaml()
        _engine = create_engine(
            f"sqlite:///{cfg.db_path}",
            connect_args={"check_same_thread": False},
        )
    return _engine


def get_conn() -> Generator[Connection, None, None]:
    with get_engine().connect() as conn:
        yield conn
