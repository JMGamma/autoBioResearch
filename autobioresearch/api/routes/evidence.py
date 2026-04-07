from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Connection

from autobioresearch.api.dependencies import get_conn
from autobioresearch.api.schemas import EvidenceItem, EvidenceResponse
from autobioresearch.storage.repositories import EntityRepo, EvidenceRepo, InteractionRepo

router = APIRouter()


@router.get("/evidence/{interaction_id}", response_model=EvidenceResponse)
def get_evidence(interaction_id: str, conn: Connection = Depends(get_conn)):
    interaction_repo = InteractionRepo(conn)
    interaction = interaction_repo.get_by_id(interaction_id)
    if not interaction:
        raise HTTPException(status_code=404, detail="Interaction not found")

    entity_repo = EntityRepo(conn)
    entity_a = entity_repo.get_by_id(interaction["entity_a_id"])
    entity_b = entity_repo.get_by_id(interaction["entity_b_id"])

    evidence_repo = EvidenceRepo(conn)
    rows = evidence_repo.get_for_interaction_with_paper_metadata(interaction_id)

    items = [
        EvidenceItem(
            id=row["id"],
            evidence_type=row["evidence_type"],
            confidence=row["confidence"],
            confidence_score=row.get("confidence_score") or 0.0,
            snippet=row.get("snippet"),
            organism=row.get("organism"),
            tissue_cell_type=row.get("tissue_cell_type"),
            condition=row.get("condition"),
            paper_id=row.get("paper_id"),
            paper_title=row.get("paper_title"),
            paper_doi=row.get("paper_doi"),
            paper_year=row.get("paper_year"),
            paper_journal=row.get("paper_journal"),
        )
        for row in rows
    ]

    return EvidenceResponse(
        interaction_id=interaction_id,
        interaction_type=interaction.get("interaction_type"),
        entity_a_name=entity_a["display_name"] if entity_a else None,
        entity_b_name=entity_b["display_name"] if entity_b else None,
        evidence=items,
    )
