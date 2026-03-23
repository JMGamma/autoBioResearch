"""
Backfill newer reliability workflows onto an existing AutoBioResearch database.

This is the safer alternative to wiping the DB after major workflow improvements.
It upgrades existing rows in place by:
  1. Applying any pending additive schema migrations
  2. Backfilling normalized evidence context fields
  3. Recomputing adjudication scores for existing evidence
  4. Reconstructing literal claim rows for legacy evidence that predates claim storage

Optional:
  5. Re-run conflict detection / conflict resolution under the newer rules

Usage:
    uv run scripts/reprocess_existing_data.py
    uv run scripts/reprocess_existing_data.py --db-path ./autobioresearch.db
    uv run scripts/reprocess_existing_data.py --refresh-conflicts
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select, update

sys.path.insert(0, str(Path(__file__).parent.parent))

from autobioresearch import database as db
from autobioresearch.config import AppConfig
from autobioresearch.conflict.adjudicator import EvidenceAdjudicator
from autobioresearch.database import create_all, init_engine
from autobioresearch.extractor.evidence_normalizer import EvidenceNormalizer
from autobioresearch.models import (
    ExtractedInteractionRaw,
    EvidenceType,
    InteractionType,
    LiteralClaimRecord,
)
from autobioresearch.storage.repositories import Repositories


def _to_interaction_type(value: str | None) -> InteractionType:
    try:
        return InteractionType(value or "unknown")
    except Exception:
        return InteractionType.UNKNOWN


def _to_evidence_type(value: str | None) -> EvidenceType:
    try:
        return EvidenceType(value or "unknown")
    except Exception:
        return EvidenceType.UNKNOWN


def backfill_reliability_fields(repos: Repositories) -> dict[str, int]:
    evidence_normalizer = EvidenceNormalizer()
    adjudicator = EvidenceAdjudicator()
    entity_a = db.entities.alias("ea")
    entity_b = db.entities.alias("eb")

    rows = repos.evidence.conn.execute(
        select(
            db.evidence,
            db.interactions.c.effect,
            db.interactions.c.direction,
            db.interactions.c.interaction_type,
            entity_a.c.canonical_name.label("entity_a_name"),
            entity_b.c.canonical_name.label("entity_b_name"),
            db.papers.c.year.label("paper_year"),
        )
        .join(db.interactions, db.interactions.c.id == db.evidence.c.interaction_id)
        .join(entity_a, entity_a.c.id == db.interactions.c.entity_a_id)
        .join(entity_b, entity_b.c.id == db.interactions.c.entity_b_id)
        .join(db.papers, db.papers.c.id == db.evidence.c.paper_id)
    ).mappings().all()

    normalized = 0
    adjudicated = 0
    claims_backfilled = 0

    for row in rows:
        raw = ExtractedInteractionRaw(
            entity_a=row["entity_a_name"],
            entity_b=row["entity_b_name"],
            interaction_type=_to_interaction_type(row.get("interaction_type")),
            direction=row.get("direction") or "undirected",
            effect=row.get("effect"),
            evidence_type=_to_evidence_type(row.get("evidence_type")),
            evidence_subtype=row.get("evidence_subtype"),
            organism=row.get("organism"),
            tissue_cell_type=row.get("tissue_cell_type"),
            condition=row.get("condition"),
            assay_type=row.get("assay_type"),
            confidence=row.get("confidence") or "low",
            confidence_score=float(row.get("confidence_score") or 0.0),
            snippet=row.get("snippet") or "",
            verification_status=row.get("verification_status"),
            verification_score=row.get("verification_score"),
            verification_notes=row.get("verification_notes") or "",
        )
        normalized_raw = evidence_normalizer.normalize(raw)

        repos.evidence.conn.execute(
            update(db.evidence)
            .where(db.evidence.c.id == row["id"])
            .values(
                normalized_organism=normalized_raw.normalized_organism,
                normalized_tissue_cell_type=normalized_raw.normalized_tissue_cell_type,
                normalized_condition=normalized_raw.normalized_condition,
                normalized_assay_type=normalized_raw.normalized_assay_type,
            )
        )
        normalized += 1

        adjudication_score, adjudication_notes = adjudicator.adjudicate(
            {
                **dict(row),
                "normalized_organism": normalized_raw.normalized_organism,
                "normalized_tissue_cell_type": normalized_raw.normalized_tissue_cell_type,
                "normalized_condition": normalized_raw.normalized_condition,
                "normalized_assay_type": normalized_raw.normalized_assay_type,
            },
            {"year": row.get("paper_year")},
        )
        repos.evidence.update_adjudication(
            row["id"],
            score=adjudication_score,
            notes=adjudication_notes,
        )
        adjudicated += 1

        if not row.get("claim_id"):
            claim_id = repos.literal_claims.insert(
                LiteralClaimRecord(
                    paper_id=row["paper_id"],
                    entity_a_text=row["entity_a_name"],
                    entity_b_text=row["entity_b_name"],
                    interaction_type_text=row.get("interaction_type") or "unknown",
                    direction_text=row.get("direction"),
                    effect_text=row.get("effect"),
                    evidence_type_text=row.get("evidence_type"),
                    organism_text=row.get("organism"),
                    tissue_cell_type_text=row.get("tissue_cell_type"),
                    condition_text=row.get("condition"),
                    assay_type_text=row.get("assay_type"),
                    evidence_subtype_text=row.get("evidence_subtype"),
                    confidence_text=row.get("confidence"),
                    confidence_score=float(row.get("confidence_score") or 0.0),
                    snippet=row.get("snippet") or "",
                    verification_status=row.get("verification_status"),
                    verification_score=row.get("verification_score"),
                    verification_notes=row.get("verification_notes"),
                )
            )
            repos.evidence.conn.execute(
                update(db.evidence)
                .where(db.evidence.c.id == row["id"])
                .values(claim_id=claim_id)
            )
            claims_backfilled += 1

    repos.evidence.conn.commit()
    return {
        "evidence_normalized": normalized,
        "evidence_adjudicated": adjudicated,
        "claims_backfilled": claims_backfilled,
    }


def refresh_conflicts(db_path: str, config_path: str) -> dict[str, int]:
    config = AppConfig.from_yaml(config_path)
    config.db_path = db_path

    from autobioresearch.extractor.claude_client import LLMClient
    from autobioresearch.conflict.detector import ConflictDetector
    from autobioresearch.conflict.resolver import ConflictResolver

    llm = LLMClient(config)
    detector = ConflictDetector()
    resolver = ConflictResolver(llm=llm)
    engine = init_engine(db_path)

    with engine.connect() as conn:
        repos = Repositories(conn)
        detected = detector.detect(repos, config)
        conn.commit()
        resolved = resolver.analyze_and_resolve(repos, config)
        conn.commit()
    return {"conflicts_detected": detected, "conflicts_resolved": resolved}


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill newer workflows onto an existing AutoBioResearch DB.")
    parser.add_argument("--db-path", default="./autobioresearch.db", help="Path to SQLite database file")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml (used only with --refresh-conflicts)")
    parser.add_argument("--refresh-conflicts", action="store_true", help="Also re-run conflict detection and resolution after backfilling")
    args = parser.parse_args()

    engine = init_engine(args.db_path)
    create_all(engine)

    with engine.connect() as conn:
        repos = Repositories(conn)
        stats = backfill_reliability_fields(repos)

    print(f"Reprocessed database at {Path(args.db_path).resolve()}")
    for key, value in stats.items():
        print(f"  {key}: {value}")

    if args.refresh_conflicts:
        conflict_stats = refresh_conflicts(args.db_path, args.config)
        for key, value in conflict_stats.items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
