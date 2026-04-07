from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Connection

from autobioresearch.api.dependencies import get_conn
from autobioresearch.api.schemas import ConflictSummary, ConflictsResponse, EntityDetail, EntitySearchResult
from autobioresearch.storage.repositories import ConflictRepo, EntityRepo, EvidenceRepo

router = APIRouter()


@router.get("/entities/search", response_model=list[EntitySearchResult])
def search_entities(
    q: str,
    entity_type: Optional[str] = None,
    organism: Optional[str] = None,
    limit: int = 20,
    conn: Connection = Depends(get_conn),
):
    if not q or not q.strip():
        raise HTTPException(status_code=422, detail="q must not be empty")
    repo = EntityRepo(conn)
    return repo.search_entities(q=q, entity_type=entity_type, organism=organism, limit=limit)


@router.get("/entities/{entity_id}", response_model=EntityDetail)
def get_entity(entity_id: str, conn: Connection = Depends(get_conn)):
    repo = EntityRepo(conn)
    entity = repo.get_by_id(entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    # Degree stats: count interactions grouped by direction role
    degree_rows = conn.execute(text("""
        SELECT
            SUM(CASE WHEN entity_a_id = :eid AND UPPER(direction) IN ('A_TO_B','BIDIRECTIONAL') THEN 1 ELSE 0 END) AS degree_out_a,
            SUM(CASE WHEN entity_b_id = :eid AND UPPER(direction) IN ('B_TO_A','BIDIRECTIONAL') THEN 1 ELSE 0 END) AS degree_out_b,
            SUM(CASE WHEN entity_b_id = :eid AND UPPER(direction) IN ('A_TO_B','BIDIRECTIONAL') THEN 1 ELSE 0 END) AS degree_in_b,
            SUM(CASE WHEN entity_a_id = :eid AND UPPER(direction) IN ('B_TO_A','BIDIRECTIONAL') THEN 1 ELSE 0 END) AS degree_in_a,
            COUNT(*) AS degree_total
        FROM interactions
        WHERE entity_a_id = :eid OR entity_b_id = :eid
    """), {"eid": entity_id}).mappings().fetchone()

    if degree_rows is None:
        degree_out = degree_in = degree_total = 0
    else:
        degree_out = (degree_rows["degree_out_a"] or 0) + (degree_rows["degree_out_b"] or 0)
        degree_in  = (degree_rows["degree_in_b"]  or 0) + (degree_rows["degree_in_a"]  or 0)
        degree_total = degree_rows["degree_total"] or 0

    # Evidence total
    evidence_total = conn.execute(text("""
        SELECT COUNT(*) FROM evidence ev
        JOIN interactions i ON ev.interaction_id = i.id
        WHERE i.entity_a_id = :eid OR i.entity_b_id = :eid
    """), {"eid": entity_id}).scalar_one()

    # Open conflicts touching this entity's interactions
    conflict_count = conn.execute(text("""
        SELECT COUNT(*) FROM conflicts c
        JOIN interactions ia ON c.interaction_a_id = ia.id
        WHERE c.status IN ('open','reopened','investigating')
          AND (ia.entity_a_id = :eid OR ia.entity_b_id = :eid)
    """), {"eid": entity_id}).scalar_one()

    return EntityDetail(
        id=entity["id"],
        display_name=entity["display_name"],
        canonical_name=entity["canonical_name"],
        entity_type=entity["entity_type"],
        organism=entity.get("organism"),
        paper_count=entity["paper_count"],
        matching_synonym=None,
        degree_in=degree_in,
        degree_out=degree_out,
        degree_total=degree_total,
        evidence_total=evidence_total or 0,
        has_conflicts=conflict_count > 0,
        open_conflict_count=conflict_count or 0,
    )


@router.get("/entities/{entity_id}/conflicts", response_model=ConflictsResponse)
def get_entity_conflicts(entity_id: str, conn: Connection = Depends(get_conn)):
    repo = EntityRepo(conn)
    if not repo.get_by_id(entity_id):
        raise HTTPException(status_code=404, detail="Entity not found")

    rows = conn.execute(text("""
        SELECT
            c.id, c.conflict_type, c.conflict_axis, c.status, c.llm_analysis,
            ia.interaction_type AS a_type, ia.effect AS a_effect,
            ea1.display_name AS a_entity_a, ea2.display_name AS a_entity_b,
            ib.interaction_type AS b_type, ib.effect AS b_effect,
            eb1.display_name AS b_entity_a, eb2.display_name AS b_entity_b
        FROM conflicts c
        JOIN interactions ia ON c.interaction_a_id = ia.id
        JOIN interactions ib ON c.interaction_b_id = ib.id
        JOIN entities ea1 ON ia.entity_a_id = ea1.id
        JOIN entities ea2 ON ia.entity_b_id = ea2.id
        JOIN entities eb1 ON ib.entity_a_id = eb1.id
        JOIN entities eb2 ON ib.entity_b_id = eb2.id
        WHERE c.status IN ('open', 'reopened', 'investigating')
          AND (ia.entity_a_id = :eid OR ia.entity_b_id = :eid)
        ORDER BY c.penalty_weight DESC
        LIMIT 50
    """), {"eid": entity_id}).mappings().fetchall()

    conflicts = [
        ConflictSummary(
            id=row["id"],
            conflict_type=row["conflict_type"],
            conflict_axis=row["conflict_axis"],
            status=row["status"],
            interaction_a_entity_a=row["a_entity_a"],
            interaction_a_entity_b=row["a_entity_b"],
            interaction_a_effect=row["a_effect"],
            interaction_a_type=row["a_type"],
            interaction_b_entity_a=row["b_entity_a"],
            interaction_b_entity_b=row["b_entity_b"],
            interaction_b_effect=row["b_effect"],
            interaction_b_type=row["b_type"],
            llm_analysis=row["llm_analysis"],
        )
        for row in rows
    ]

    return ConflictsResponse(entity_id=entity_id, conflicts=conflicts)
