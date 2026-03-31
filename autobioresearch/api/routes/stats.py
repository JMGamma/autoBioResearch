from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.engine import Connection

from autobioresearch.api.dependencies import get_conn
from autobioresearch.api.schemas import StatsResponse
from autobioresearch.storage.repositories import (
    ConflictRepo,
    EntityRepo,
    EvidenceRepo,
    InteractionRepo,
    PaperRepo,
)

router = APIRouter()


@router.get("/stats", response_model=StatsResponse)
def get_stats(conn: Connection = Depends(get_conn)):
    n_entities = EntityRepo(conn).count()
    n_interactions = InteractionRepo(conn).count()
    n_evidence = EvidenceRepo(conn).count()
    n_open_conflicts = ConflictRepo(conn).count_open()
    n_papers = PaperRepo(conn).count()

    last_row = conn.execute(text("""
        SELECT score, timestamp FROM metrics_log ORDER BY id DESC LIMIT 1
    """)).mappings().fetchone()

    return StatsResponse(
        n_entities=n_entities,
        n_interactions=n_interactions,
        n_evidence=n_evidence,
        n_open_conflicts=n_open_conflicts,
        n_papers=n_papers,
        last_score=last_row["score"] if last_row else None,
        last_cycle_timestamp=last_row["timestamp"] if last_row else None,
    )
