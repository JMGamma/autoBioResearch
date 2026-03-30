"""
Repository layer: typed read/write operations for all DB tables.
All methods use SQLAlchemy Core (no ORM) with explicit SQL.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Connection, delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from autobioresearch import database as db
from autobioresearch.models import (
    BiologicalEntity,
    Conflict,
    ConflictStatus,
    EvidenceRecord,
    Interaction,
    LiteralClaimRecord,
    Paper,
    SearchQuery,
)

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Paper repository
# ---------------------------------------------------------------------------

class PaperRepo:
    def __init__(self, conn: Connection):
        self.conn = conn

    def upsert(self, paper: Paper) -> bool:
        """Insert paper if not exists. Returns True if newly inserted."""
        existing = self.conn.execute(
            select(db.papers.c.id).where(db.papers.c.id == paper.id)
        ).fetchone()
        if existing:
            # Update query_ids and promote priority if this paper is from a higher-priority query
            row = self.conn.execute(
                select(db.papers.c.query_ids, db.papers.c.priority).where(db.papers.c.id == paper.id)
            ).fetchone()
            existing_ids = json.loads(row.query_ids or "[]")
            merged = list(set(existing_ids + paper.query_ids)) if paper.query_ids else existing_ids
            new_priority = max(int(row.priority or 0), paper.priority)
            self.conn.execute(
                update(db.papers)
                .where(db.papers.c.id == paper.id)
                .values(query_ids=json.dumps(merged), priority=new_priority, updated_at=_now())
            )
            return False

        self.conn.execute(db.papers.insert().values(
            id=paper.id,
            source=paper.source,
            title=paper.title,
            abstract=paper.abstract,
            authors=json.dumps(paper.authors),
            journal=paper.journal,
            year=paper.year,
            doi=paper.doi,
            pmc_id=paper.pmc_id,
            fetch_status=paper.fetch_status.value,
            extraction_status=paper.extraction_status.value,
            query_ids=json.dumps(paper.query_ids),
            priority=paper.priority,
            created_at=_now(),
            updated_at=_now(),
        ))
        return True

    def upsert_many(self, papers: list[Paper]) -> int:
        """Returns count of newly inserted papers."""
        return sum(self.upsert(p) for p in papers)

    def get_pending_extraction(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            select(db.papers)
            .where(db.papers.c.extraction_status == "pending")
            .where(db.papers.c.fetch_status.in_(["abstract_only", "full_text_available"]))
            .order_by(db.papers.c.priority.desc(), db.papers.c.created_at)
            .limit(limit)
        ).mappings().all()
        return [dict(r) for r in rows]

    def mark_extraction_done(self, paper_id: str):
        self.conn.execute(
            update(db.papers)
            .where(db.papers.c.id == paper_id)
            .values(
                extraction_status="done",
                updated_at=_now(),
            )
        )

    def mark_extraction_failed(self, paper_id: str, error: str):
        self.conn.execute(
            update(db.papers)
            .where(db.papers.c.id == paper_id)
            .values(
                extraction_status="failed",
                extraction_error=error[:2000],
                updated_at=_now(),
            )
        )

    def count(self) -> int:
        return self.conn.execute(select(func.count()).select_from(db.papers)).scalar_one()

    def count_by_extraction_status(self, status: str) -> int:
        return self.conn.execute(
            select(func.count())
            .select_from(db.papers)
            .where(db.papers.c.extraction_status == status)
        ).scalar_one()

    def get_by_id(self, paper_id: str) -> Optional[dict]:
        row = self.conn.execute(
            select(db.papers).where(db.papers.c.id == paper_id)
        ).mappings().fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Entity repository
# ---------------------------------------------------------------------------

class EntityRepo:
    def __init__(self, conn: Connection):
        self.conn = conn

    def insert(self, entity: BiologicalEntity) -> str:
        """Insert a new entity. Returns its id (new or pre-existing).

        On a UNIQUE constraint collision on (canonical_name, organism) — which
        can happen when two isoform accessions for the same gene resolve to the
        same canonical name — the existing row's id is returned instead of
        raising, keeping the seeder idempotent across re-runs and isoforms.
        """
        try:
            self.conn.execute(db.entities.insert().values(
                id=entity.id,
                canonical_name=entity.canonical_name,
                display_name=entity.display_name,
                entity_type=entity.entity_type.value,
                synonyms=json.dumps(entity.synonyms),
                external_ids=json.dumps(entity.external_ids),
                organism=entity.organism,
                description=entity.description,
                paper_count=entity.paper_count,
                created_at=_now(),
                updated_at=_now(),
            ))
            return entity.id
        except IntegrityError:
            # Duplicate (canonical_name, organism) — return the existing row's id
            row = self.conn.execute(
                select(db.entities.c.id).where(
                    (db.entities.c.canonical_name == entity.canonical_name)
                    & (db.entities.c.organism == entity.organism)
                )
            ).fetchone()
            if row:
                logger.debug(
                    f"EntityRepo.insert: {entity.canonical_name} already exists "
                    f"({entity.organism}), reusing id {row.id}"
                )
                return row.id
            raise  # different constraint; let it propagate

    def add_synonyms(self, entity_id: str, synonyms: list[str], source: str = "llm_extracted"):
        for syn in synonyms:
            syn_lower = syn.lower().strip()
            if not syn_lower:
                continue
            try:
                self.conn.execute(db.entity_synonyms.insert().values(
                    entity_id=entity_id,
                    synonym=syn_lower,
                    source=source,
                ))
            except Exception:
                pass  # unique constraint violation = synonym already known

    def find_by_synonym_candidates(self, synonym: str) -> list[dict]:
        rows = self.conn.execute(
            select(
                db.entity_synonyms.c.entity_id,
                db.entities.c.entity_type,
                db.entities.c.organism,
                db.entities.c.canonical_name,
                db.entities.c.paper_count,
                db.entity_synonyms.c.source,
            )
            .join(db.entities, db.entities.c.id == db.entity_synonyms.c.entity_id)
            .where(db.entity_synonyms.c.synonym == synonym.lower().strip())
            .order_by(db.entities.c.paper_count.desc(), db.entities.c.canonical_name.asc())
        ).mappings().all()
        return [dict(r) for r in rows]

    def find_by_synonym(
        self,
        synonym: str,
        *,
        entity_type: Optional[str] = None,
        organism: Optional[str] = None,
    ) -> Optional[str]:
        """Returns entity_id when the synonym lookup is unambiguous after optional narrowing."""
        candidates = self.find_by_synonym_candidates(synonym)
        if not candidates:
            return None

        unique_ids = {row["entity_id"] for row in candidates}
        if len(unique_ids) == 1:
            return next(iter(unique_ids))

        narrowed = candidates
        if entity_type is not None:
            narrowed = [row for row in narrowed if row.get("entity_type") == entity_type]
            unique_ids = {row["entity_id"] for row in narrowed}
            if len(unique_ids) == 1:
                return next(iter(unique_ids))

        if organism is not None:
            organism_lower = organism.lower().strip()
            narrowed = [
                row for row in narrowed
                if (row.get("organism") or "").lower().strip() == organism_lower
            ]
            unique_ids = {row["entity_id"] for row in narrowed}
            if len(unique_ids) == 1:
                return next(iter(unique_ids))

        return None

    def find_by_external_id(self, source: str, accession: str) -> Optional[str]:
        """Look up entity_id by external_ids JSON field (e.g. source='uniprot', accession='P04637').
        Catches LLM-pipeline entities that have the accession in external_ids but not as a synonym."""
        row = self.conn.execute(
            text("SELECT id FROM entities WHERE json_extract(external_ids, :key) = :val"),
            {"key": f"$.{source}", "val": accession}
        ).fetchone()
        return row.id if row else None

    def get_by_id(self, entity_id: str) -> Optional[dict]:
        row = self.conn.execute(
            select(db.entities).where(db.entities.c.id == entity_id)
        ).mappings().fetchone()
        return dict(row) if row else None

    def get_all_canonical_names(self, entity_type: Optional[str] = None) -> list[tuple[str, str]]:
        """Returns list of (canonical_name, id) pairs."""
        q = select(db.entities.c.canonical_name, db.entities.c.id)
        if entity_type:
            q = q.where(db.entities.c.entity_type == entity_type)
        return [(r.canonical_name, r.id) for r in self.conn.execute(q)]

    def get_all_synonyms_for_type(self, entity_type: Optional[str] = None) -> list[tuple[str, str]]:
        """
        Returns list of (synonym, entity_id) for all entities of the given type(s).
        Used by fuzzy matching so that known synonyms are candidates, not just canonical names.
        """
        # Join entity_synonyms → entities to filter by type
        q = (
            select(db.entity_synonyms.c.synonym, db.entity_synonyms.c.entity_id)
            .join(db.entities, db.entities.c.id == db.entity_synonyms.c.entity_id)
        )
        if entity_type:
            q = q.where(db.entities.c.entity_type == entity_type)
        return [(r.synonym, r.entity_id) for r in self.conn.execute(q)]

    def increment_paper_count(self, entity_id: str):
        self.conn.execute(
            update(db.entities)
            .where(db.entities.c.id == entity_id)
            .values(
                paper_count=db.entities.c.paper_count + 1,
                updated_at=_now(),
            )
        )

    def get_low_interaction_entities(self, min_interactions: int, limit: int = 50) -> list[dict]:
        """Entities with fewer than min_interactions for gap-filling query generation."""
        involvement = (
            select(db.interactions.c.entity_a_id.label("entity_id"))
            .union_all(
                select(db.interactions.c.entity_b_id.label("entity_id"))
            )
            .subquery()
        )
        subq = (
            select(involvement.c.entity_id, func.count().label("cnt"))
            .group_by(involvement.c.entity_id)
            .subquery()
        )
        rows = self.conn.execute(
            select(db.entities)
            .outerjoin(subq, db.entities.c.id == subq.c.entity_id)
            .where(func.coalesce(subq.c.cnt, 0) < min_interactions)
            .limit(limit)
        ).mappings().all()
        return [dict(r) for r in rows]

    def get_sparse_high_value_entities(self, min_interactions: int, limit: int = 20) -> list[dict]:
        involvement = (
            select(db.interactions.c.entity_a_id.label("entity_id"))
            .union_all(select(db.interactions.c.entity_b_id.label("entity_id")))
            .subquery()
        )
        counts = (
            select(involvement.c.entity_id, func.count().label("interaction_count"))
            .group_by(involvement.c.entity_id)
            .subquery()
        )
        rows = self.conn.execute(
            select(
                db.entities,
                func.coalesce(counts.c.interaction_count, 0).label("interaction_count"),
            )
            .outerjoin(counts, db.entities.c.id == counts.c.entity_id)
            .where(func.coalesce(counts.c.interaction_count, 0) < min_interactions)
            .order_by(db.entities.c.paper_count.desc(), func.coalesce(counts.c.interaction_count, 0).asc())
            .limit(limit)
        ).mappings().all()
        return [dict(r) for r in rows]

    def search_entities(
        self,
        q: str,
        entity_type: Optional[str] = None,
        organism: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """
        Prefix search over entity_synonyms for typeahead/search pages.

        Returns entities whose synonyms start with q (case-insensitive), ranked
        by exact match first, then paper_count DESC.  DISTINCT on entity_id so
        multiple matching synonyms don't produce duplicate rows.
        """
        q_lower = q.lower().strip()
        sql = text("""
            SELECT
                e.id,
                e.display_name,
                e.canonical_name,
                e.entity_type,
                e.organism,
                e.paper_count,
                es.synonym AS matching_synonym,
                CASE WHEN es.synonym = :exact THEN 0 ELSE 1 END AS rank_order
            FROM entity_synonyms es
            JOIN entities e ON e.id = es.entity_id
            WHERE es.synonym LIKE :prefix
              AND (:entity_type IS NULL OR e.entity_type = :entity_type)
              AND (:organism_lower IS NULL OR LOWER(COALESCE(e.organism, '')) = :organism_lower)
            GROUP BY e.id
            ORDER BY rank_order ASC, e.paper_count DESC
            LIMIT :limit
        """)
        rows = self.conn.execute(sql, {
            "exact": q_lower,
            "prefix": q_lower + "%",
            "entity_type": entity_type,
            "organism_lower": organism.lower().strip() if organism else None,
            "limit": min(limit, 50),
        }).mappings().all()
        return [dict(r) for r in rows]

    def count(self) -> int:
        return self.conn.execute(select(func.count()).select_from(db.entities)).scalar_one()


# ---------------------------------------------------------------------------
# Interaction repository
# ---------------------------------------------------------------------------

class InteractionRepo:
    def __init__(self, conn: Connection):
        self.conn = conn

    def upsert(self, interaction: Interaction) -> tuple[str, bool]:
        """
        Upsert based on (entity_a_id, entity_b_id, interaction_type, effect, direction).
        Returns (interaction_id, is_new).
        """
        row = self.conn.execute(
            select(db.interactions.c.id)
            .where(db.interactions.c.entity_a_id == interaction.entity_a_id)
            .where(db.interactions.c.entity_b_id == interaction.entity_b_id)
            .where(db.interactions.c.interaction_type == interaction.interaction_type.value)
            .where(db.interactions.c.effect == interaction.effect)
            .where(db.interactions.c.direction == interaction.direction)
        ).fetchone()

        if row:
            return row.id, False

        self.conn.execute(db.interactions.insert().values(
            id=interaction.id,
            entity_a_id=interaction.entity_a_id,
            entity_b_id=interaction.entity_b_id,
            interaction_type=interaction.interaction_type.value,
            direction=interaction.direction,
            effect=interaction.effect,
            evidence_count=0,
            composite_confidence="low",
            composite_confidence_score=0.0,
            created_at=_now(),
            updated_at=_now(),
        ))
        return interaction.id, True

    def update_composite_confidence(self, interaction_id: str):
        """Recompute composite confidence from all evidence for this interaction."""
        rows = self.conn.execute(
            select(
                db.evidence.c.confidence_score,
                db.evidence.c.evidence_type,
            ).where(db.evidence.c.interaction_id == interaction_id)
        ).all()

        if not rows:
            return

        # Evidence type quality weights
        type_weights = {
            "structural": 1.5,
            "in_vivo": 1.3,
            "in_vitro": 1.0,
            "curated_db": 0.9,
            "genetic_screen": 0.9,
            "clinical": 1.1,
            "co_expression": 0.6,
            "computational": 0.5,
            "unknown": 0.7,
        }

        total_weight = 0.0
        weighted_sum = 0.0
        for r in rows:
            w = type_weights.get(r.evidence_type, 0.7)
            weighted_sum += r.confidence_score * w
            total_weight += w

        score = weighted_sum / total_weight if total_weight > 0 else 0.0
        count = len(rows)

        # Threshold rules
        if count >= 3 and score >= 0.5:
            confidence = "high"
        elif score >= 0.65 or (count >= 2 and score >= 0.55):
            confidence = "high"
        elif score >= 0.4:
            confidence = "medium"
        else:
            confidence = "low"

        self.conn.execute(
            update(db.interactions)
            .where(db.interactions.c.id == interaction_id)
            .values(
                evidence_count=count,
                composite_confidence=confidence,
                composite_confidence_score=round(score, 4),
                updated_at=_now(),
            )
        )

    def get_by_id(self, interaction_id: str) -> Optional[dict]:
        row = self.conn.execute(
            select(db.interactions).where(db.interactions.c.id == interaction_id)
        ).mappings().fetchone()
        return dict(row) if row else None

    def get_pairs_for_conflict_detection(
        self,
        limit: int = 500,
        already_checked: Optional[set[frozenset]] = None,
    ) -> list[tuple[dict, dict]]:
        """
        Return pairs of interactions on the same entity pair + type with either
        different effects or different directed claims.
        Excludes pairs already in the conflicts table.
        """
        # Split the OR join into two UNION ALL branches so SQLite can use the
        # composite indexes (idx_interactions_conflict_fwd / _rev) on each branch
        # independently.  A single OR join condition prevents index use entirely,
        # causing an O(n²) full scan that hangs on large databases.
        stmt = text("""
            SELECT a.id AS a_id, b.id AS b_id
            FROM interactions a
            JOIN interactions b
              ON a.entity_a_id = b.entity_a_id
             AND a.entity_b_id = b.entity_b_id
             AND a.interaction_type = b.interaction_type
             AND a.id < b.id
            WHERE (
                    (a.effect IS NOT NULL AND b.effect IS NOT NULL AND a.effect != b.effect)
                    OR (
                        COALESCE(a.direction, 'undirected') != COALESCE(b.direction, 'undirected')
                        AND 'undirected' NOT IN (
                            COALESCE(a.direction, 'undirected'),
                            COALESCE(b.direction, 'undirected')
                        )
                    )
                  )
              -- Require independent sources (papers unique to each side).
              AND EXISTS (
                  SELECT 1 FROM evidence ea
                  WHERE ea.interaction_id = a.id
                    AND NOT EXISTS (
                        SELECT 1 FROM evidence eb
                        WHERE eb.interaction_id = b.id AND eb.paper_id = ea.paper_id
                    )
              )
              AND EXISTS (
                  SELECT 1 FROM evidence eb
                  WHERE eb.interaction_id = b.id
                    AND NOT EXISTS (
                        SELECT 1 FROM evidence ea
                        WHERE ea.interaction_id = a.id AND ea.paper_id = eb.paper_id
                    )
              )
              AND NOT EXISTS (
                  SELECT 1 FROM conflicts
                  WHERE (interaction_a_id = a.id AND interaction_b_id = b.id)
                     OR (interaction_a_id = b.id AND interaction_b_id = a.id)
              )

            UNION ALL

            -- Reversed entity-pair order (A→B stored as B→A in one interaction).
            SELECT a.id AS a_id, b.id AS b_id
            FROM interactions a
            JOIN interactions b
              ON a.entity_a_id = b.entity_b_id
             AND a.entity_b_id = b.entity_a_id
             AND a.interaction_type = b.interaction_type
             AND a.id < b.id
            WHERE (
                    (a.effect IS NOT NULL AND b.effect IS NOT NULL AND a.effect != b.effect)
                    OR (
                        COALESCE(a.direction, 'undirected') != COALESCE(b.direction, 'undirected')
                        AND 'undirected' NOT IN (
                            COALESCE(a.direction, 'undirected'),
                            COALESCE(b.direction, 'undirected')
                        )
                    )
                  )
              AND EXISTS (
                  SELECT 1 FROM evidence ea
                  WHERE ea.interaction_id = a.id
                    AND NOT EXISTS (
                        SELECT 1 FROM evidence eb
                        WHERE eb.interaction_id = b.id AND eb.paper_id = ea.paper_id
                    )
              )
              AND EXISTS (
                  SELECT 1 FROM evidence eb
                  WHERE eb.interaction_id = b.id
                    AND NOT EXISTS (
                        SELECT 1 FROM evidence ea
                        WHERE ea.interaction_id = a.id AND ea.paper_id = eb.paper_id
                    )
              )
              AND NOT EXISTS (
                  SELECT 1 FROM conflicts
                  WHERE (interaction_a_id = a.id AND interaction_b_id = b.id)
                     OR (interaction_a_id = b.id AND interaction_b_id = a.id)
              )

            LIMIT :limit
        """)
        rows = self.conn.execute(stmt, {"limit": limit}).all()
        pairs = []
        for row in rows:
            a = self.get_by_id(row.a_id)
            b = self.get_by_id(row.b_id)
            if a and b:
                pairs.append((a, b))
        return pairs

    def count(self) -> int:
        return self.conn.execute(select(func.count()).select_from(db.interactions)).scalar_one()

    def get_under_supported_seeded_interactions(self, max_evidence: int, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(text("""
            SELECT
                i.id,
                i.interaction_type,
                i.direction,
                i.effect,
                i.evidence_count,
                i.composite_confidence,
                i.composite_confidence_score,
                ea.canonical_name AS entity_a_name,
                eb.canonical_name AS entity_b_name
            FROM interactions i
            JOIN evidence e ON e.interaction_id = i.id
            JOIN entities ea ON ea.id = i.entity_a_id
            JOIN entities eb ON eb.id = i.entity_b_id
            WHERE e.evidence_type = 'curated_db'
              AND i.evidence_count <= :max_evidence
            GROUP BY i.id
            ORDER BY i.composite_confidence_score DESC, i.evidence_count ASC
            LIMIT :limit
        """), {"max_evidence": max_evidence, "limit": limit}).mappings().all()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Evidence repository
# ---------------------------------------------------------------------------

class EvidenceRepo:
    def __init__(self, conn: Connection):
        self.conn = conn

    def insert(self, ev: EvidenceRecord) -> bool:
        """Insert evidence. Returns True if newly inserted (False if duplicate paper+interaction)."""
        existing = self.conn.execute(
            select(db.evidence.c.id)
            .where(db.evidence.c.interaction_id == ev.interaction_id)
            .where(db.evidence.c.paper_id == ev.paper_id)
        ).fetchone()
        if existing:
            return False

        self.conn.execute(db.evidence.insert().values(
            id=ev.id,
            claim_id=ev.claim_id,
            interaction_id=ev.interaction_id,
            paper_id=ev.paper_id,
            evidence_type=ev.evidence_type.value,
            evidence_subtype=ev.context.evidence_subtype,
            confidence=ev.confidence,
            confidence_score=ev.confidence_score,
            organism=ev.context.organism,
            tissue_cell_type=ev.context.tissue_cell_type,
            condition=ev.context.condition,
            temperature=ev.context.temperature,
            concentration=ev.context.concentration,
            assay_type=ev.context.assay_type,
            normalized_organism=ev.context.normalized_organism,
            normalized_tissue_cell_type=ev.context.normalized_tissue_cell_type,
            normalized_condition=ev.context.normalized_condition,
            normalized_assay_type=ev.context.normalized_assay_type,
            snippet=ev.snippet[:400],
            snippet_start=ev.snippet_start,
            snippet_end=ev.snippet_end,
            verification_status=ev.verification_status,
            verification_score=ev.verification_score,
            verification_notes=ev.verification_notes[:500] if ev.verification_notes else None,
            adjudication_score=ev.adjudication_score,
            adjudication_notes=ev.adjudication_notes[:500] if ev.adjudication_notes else None,
            created_at=_now(),
        ))
        return True

    def get_for_interaction(self, interaction_id: str) -> list[dict]:
        rows = self.conn.execute(
            select(db.evidence).where(db.evidence.c.interaction_id == interaction_id)
        ).mappings().all()
        return [dict(r) for r in rows]

    def get_for_interaction_with_paper_metadata(self, interaction_id: str) -> list[dict]:
        rows = self.conn.execute(
            select(
                db.evidence,
                db.papers.c.year.label("paper_year"),
                db.papers.c.title.label("paper_title"),
                db.papers.c.doi.label("paper_doi"),
                db.papers.c.journal.label("paper_journal"),
            )
            .join(db.papers, db.papers.c.id == db.evidence.c.paper_id)
            .where(db.evidence.c.interaction_id == interaction_id)
        ).mappings().all()
        return [dict(r) for r in rows]

    def get_for_paper(self, paper_id: str) -> list[dict]:
        rows = self.conn.execute(
            select(db.evidence).where(db.evidence.c.paper_id == paper_id)
        ).mappings().all()
        return [dict(r) for r in rows]

    def count(self) -> int:
        return self.conn.execute(select(func.count()).select_from(db.evidence)).scalar_one()

    def count_by_verification_status(self, status: str) -> int:
        return self.conn.execute(
            select(func.count())
            .select_from(db.evidence)
            .where(db.evidence.c.verification_status == status)
        ).scalar_one()

    def get_conflict_verification_candidates(self, limit: int = 25) -> list[dict]:
        rows = self.conn.execute(text("""
            SELECT
                e.id AS evidence_id,
                e.interaction_id,
                e.paper_id,
                e.organism,
                e.tissue_cell_type,
                e.condition,
                e.snippet,
                e.confidence,
                e.confidence_score,
                i.interaction_type,
                i.direction,
                i.effect,
                pa.title AS paper_title,
                pa.abstract AS paper_abstract,
                ea.canonical_name AS entity_a_name,
                eb.canonical_name AS entity_b_name
            FROM evidence e
            JOIN interactions i ON i.id = e.interaction_id
            JOIN papers pa ON pa.id = e.paper_id
            JOIN entities ea ON ea.id = i.entity_a_id
            JOIN entities eb ON eb.id = i.entity_b_id
            WHERE EXISTS (
                SELECT 1 FROM conflicts c
                WHERE c.status IN ('open', 'investigating', 'reopened')
                  AND (c.interaction_a_id = e.interaction_id OR c.interaction_b_id = e.interaction_id)
            )
              AND (e.verification_status IS NULL OR e.verification_status = 'needs_review')
            ORDER BY e.confidence_score DESC, e.created_at DESC
            LIMIT :limit
        """), {"limit": limit}).mappings().all()
        return [dict(r) for r in rows]

    def update_verification(
        self,
        evidence_id: str,
        *,
        status: str,
        score: float | None,
        notes: str | None,
    ):
        self.conn.execute(
            update(db.evidence)
            .where(db.evidence.c.id == evidence_id)
            .values(
                verification_status=status,
                verification_score=score,
                verification_notes=notes[:500] if notes else None,
            )
        )

    def update_adjudication(
        self,
        evidence_id: str,
        *,
        score: float | None,
        notes: str | None,
    ):
        self.conn.execute(
            update(db.evidence)
            .where(db.evidence.c.id == evidence_id)
            .values(
                adjudication_score=score,
                adjudication_notes=notes[:500] if notes else None,
            )
        )


class LiteralClaimRepo:
    def __init__(self, conn: Connection):
        self.conn = conn

    def insert(self, claim: LiteralClaimRecord) -> str:
        self.conn.execute(db.literal_claims.insert().values(
            id=claim.id,
            paper_id=claim.paper_id,
            entity_a_text=claim.entity_a_text,
            entity_b_text=claim.entity_b_text,
            interaction_type_text=claim.interaction_type_text,
            direction_text=claim.direction_text,
            effect_text=claim.effect_text,
            evidence_type_text=claim.evidence_type_text,
            organism_text=claim.organism_text,
            tissue_cell_type_text=claim.tissue_cell_type_text,
            condition_text=claim.condition_text,
            assay_type_text=claim.assay_type_text,
            evidence_subtype_text=claim.evidence_subtype_text,
            confidence_text=claim.confidence_text,
            confidence_score=claim.confidence_score,
            snippet=claim.snippet[:400],
            reasoning=claim.reasoning[:5000] if claim.reasoning else None,
            verification_status=claim.verification_status,
            verification_score=claim.verification_score,
            verification_notes=claim.verification_notes[:500] if claim.verification_notes else None,
            created_at=_now(),
        ))
        return claim.id


# ---------------------------------------------------------------------------
# Conflict repository
# ---------------------------------------------------------------------------

class ConflictRepo:
    def __init__(self, conn: Connection):
        self.conn = conn  # exposed so ConflictResolver can do direct updates

    def insert(self, conflict: Conflict):
        self.conn.execute(db.conflicts.insert().values(
            id=conflict.id,
            interaction_a_id=conflict.interaction_a_id,
            interaction_b_id=conflict.interaction_b_id,
            conflict_type=conflict.conflict_type.value,
            conflict_axis=conflict.conflict_axis,
            context_difference=json.dumps(conflict.context_difference),
            status=conflict.status.value,
            resolution_paper_id=conflict.resolution_paper_id,
            resolution_note=conflict.resolution_note,
            llm_analysis=conflict.llm_analysis,
            generated_query_ids=json.dumps(conflict.generated_query_ids),
            penalty_weight=conflict.penalty_weight,
            created_at=_now(),
            updated_at=_now(),
        ))

    def insert_many(self, conflicts: list[Conflict]):
        for c in conflicts:
            self.insert(c)

    def get_open(self, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            select(db.conflicts)
            .where(db.conflicts.c.status.in_(["open", "reopened"]))
            .order_by(db.conflicts.c.penalty_weight.desc())
            .limit(limit)
        ).mappings().all()
        return [dict(r) for r in rows]

    def update_status(
        self,
        conflict_id: str,
        status: str,
        note: Optional[str] = None,
        resolution_paper_id: Optional[str] = None,
        llm_analysis: Optional[str] = None,
        penalty_weight: Optional[float] = None,
    ):
        vals: dict = {"status": status, "updated_at": _now()}
        if note:
            vals["resolution_note"] = note
        if resolution_paper_id:
            vals["resolution_paper_id"] = resolution_paper_id
        if llm_analysis:
            vals["llm_analysis"] = llm_analysis
        if penalty_weight is not None:
            vals["penalty_weight"] = penalty_weight
        self.conn.execute(
            update(db.conflicts).where(db.conflicts.c.id == conflict_id).values(**vals)
        )

    def add_generated_queries(self, conflict_id: str, query_ids: list[str]):
        row = self.conn.execute(
            select(db.conflicts.c.generated_query_ids)
            .where(db.conflicts.c.id == conflict_id)
        ).fetchone()
        existing = json.loads(row.generated_query_ids or "[]")
        merged = list(set(existing + query_ids))
        self.conn.execute(
            update(db.conflicts)
            .where(db.conflicts.c.id == conflict_id)
            .values(generated_query_ids=json.dumps(merged), updated_at=_now())
        )

    def count_open(self) -> int:
        return self.conn.execute(
            select(func.count())
            .select_from(db.conflicts)
            .where(db.conflicts.c.status.in_(["open", "reopened"]))
        ).scalar_one()

    def count_unresolved(self) -> int:
        return self.conn.execute(
            select(func.count())
            .select_from(db.conflicts)
            .where(db.conflicts.c.status.in_(["open", "investigating", "reopened"]))
        ).scalar_one()

    def weighted_sum_open(self) -> float:
        """Sum of penalty_weight for all open conflicts."""
        result = self.conn.execute(
            select(func.sum(db.conflicts.c.penalty_weight))
            .where(db.conflicts.c.status.in_(["open", "reopened"]))
        ).scalar()
        return float(result or 0.0)

    def weighted_sum_unresolved(self) -> float:
        """Sum of penalty_weight for all unresolved conflicts."""
        result = self.conn.execute(
            select(func.sum(db.conflicts.c.penalty_weight))
            .where(db.conflicts.c.status.in_(["open", "investigating", "reopened"]))
        ).scalar()
        return float(result or 0.0)

    def reopen_for_interactions(self, interaction_ids: list[str]) -> int:
        """Reopen conflicts linked to interactions that gained new evidence."""
        if not interaction_ids:
            return 0

        result = self.conn.execute(
            update(db.conflicts)
            .where(db.conflicts.c.status.in_(["investigating", "resolved", "dismissed"]))
            .where(
                db.conflicts.c.interaction_a_id.in_(interaction_ids)
                | db.conflicts.c.interaction_b_id.in_(interaction_ids)
            )
            .values(
                status="reopened",
                resolution_paper_id=None,
                resolution_note="Reopened after new supporting evidence landed.",
                updated_at=_now(),
            )
        )
        return int(result.rowcount or 0)

    def count(self) -> int:
        return self.conn.execute(select(func.count()).select_from(db.conflicts)).scalar_one()

    def count_resolved(self) -> int:
        return self.conn.execute(
            select(func.count())
            .select_from(db.conflicts)
            .where(db.conflicts.c.status == "resolved")
        ).scalar_one()

    def get_unresolved_conflict_hubs(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(text("""
            SELECT
                c.id,
                c.penalty_weight,
                c.conflict_type,
                c.conflict_axis,
                ia.id AS interaction_a_id,
                ib.id AS interaction_b_id,
                ea.canonical_name AS entity_a_name,
                eb.canonical_name AS entity_b_name,
                ia.interaction_type AS interaction_type_a,
                ib.interaction_type AS interaction_type_b
            FROM conflicts c
            JOIN interactions ia ON ia.id = c.interaction_a_id
            JOIN interactions ib ON ib.id = c.interaction_b_id
            JOIN entities ea ON ea.id = ia.entity_a_id
            JOIN entities eb ON eb.id = ia.entity_b_id
            WHERE c.status IN ('open', 'investigating', 'reopened')
            ORDER BY c.penalty_weight DESC, c.updated_at ASC
            LIMIT :limit
        """), {"limit": limit}).mappings().all()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Query repository
# ---------------------------------------------------------------------------

class QueryRepo:
    def __init__(self, conn: Connection):
        self.conn = conn

    def insert(self, query: SearchQuery):
        existing = self.conn.execute(
            select(db.search_queries.c.id)
            .where(db.search_queries.c.query_text == query.query_text)
            .where(db.search_queries.c.source_api == query.source_api)
            .where(db.search_queries.c.origin == query.origin)
            .where(db.search_queries.c.status.in_(["pending", "running", "done"]))
        ).fetchone()
        if existing:
            return False
        self.conn.execute(db.search_queries.insert().values(
            id=query.id,
            query_text=query.query_text,
            source_api=query.source_api,
            query_type=query.query_type.value,
            origin=query.origin,
            generation_reason=query.generation_reason,
            target_kind=query.target_kind,
            target_id=query.target_id,
            planning_score=query.planning_score,
            planning_factors=json.dumps(query.planning_factors),
            status=query.status,
            papers_found=query.papers_found,
            papers_new=query.papers_new,
            outcome_papers_processed=query.outcome_papers_processed,
            outcome_new_interactions=query.outcome_new_interactions,
            outcome_new_evidence=query.outcome_new_evidence,
            attributed_papers_processed=query.attributed_papers_processed,
            attributed_new_interactions=query.attributed_new_interactions,
            attributed_new_evidence=query.attributed_new_evidence,
            improved_graph=query.improved_graph,
            last_error=None,
            created_at=_now(),
            executed_at=query.executed_at,
            completed_at=None,
        ))
        return True

    def insert_many(self, queries: list[SearchQuery]):
        inserted = 0
        for q in queries:
            inserted += 1 if self.insert(q) else 0
        return inserted

    def get_pending(self, limit: int = 10) -> list[dict]:
        rows = self.conn.execute(
            select(db.search_queries)
            .where(db.search_queries.c.status == "pending")
            .order_by(db.search_queries.c.planning_score.desc(), db.search_queries.c.created_at)
            .limit(limit)
        ).mappings().all()
        return [dict(r) for r in rows]

    def get_pending_candidates(self, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            select(db.search_queries)
            .where(db.search_queries.c.status == "pending")
            .order_by(db.search_queries.c.created_at)
            .limit(limit)
        ).mappings().all()
        return [dict(r) for r in rows]

    def update_planning(
        self,
        query_id: str,
        *,
        planning_score: float,
        planning_factors: dict | None = None,
    ):
        self.conn.execute(
            update(db.search_queries)
            .where(db.search_queries.c.id == query_id)
            .values(
                planning_score=planning_score,
                planning_factors=json.dumps(planning_factors or {}),
            )
        )

    def mark_running(self, query_id: str):
        self.conn.execute(
            update(db.search_queries)
            .where(db.search_queries.c.id == query_id)
            .where(db.search_queries.c.status == "pending")
            .values(status="running", executed_at=_now(), last_error=None)
        )

    def mark_done(self, query_id: str, papers_found: int, papers_new: int):
        self.conn.execute(
            update(db.search_queries)
            .where(db.search_queries.c.id == query_id)
            .where(db.search_queries.c.status.in_(["pending", "running"]))
            .values(
                status="done",
                papers_found=papers_found,
                papers_new=papers_new,
                improved_graph=1 if papers_new > 0 else 0,
                last_error=None,
                completed_at=_now(),
            )
        )

    def mark_failed(self, query_id: str, error: Optional[str] = None):
        self.conn.execute(
            update(db.search_queries)
            .where(db.search_queries.c.id == query_id)
            .where(db.search_queries.c.status.in_(["pending", "running"]))
            .values(status="failed", last_error=(error or "")[:2000] or None, completed_at=_now())
        )

    def count_pending(self) -> int:
        return self.conn.execute(
            select(func.count())
            .select_from(db.search_queries)
            .where(db.search_queries.c.status == "pending")
        ).scalar_one()

    def record_outcomes(
        self,
        query_ids: list[str],
        *,
        papers_processed: int,
        new_interactions: int,
        new_evidence: int,
    ):
        if not query_ids:
            return
        weight = 1.0 / len(query_ids)
        self.conn.execute(
            update(db.search_queries)
            .where(db.search_queries.c.id.in_(query_ids))
            .values(
                outcome_papers_processed=db.search_queries.c.outcome_papers_processed + papers_processed,
                outcome_new_interactions=db.search_queries.c.outcome_new_interactions + new_interactions,
                outcome_new_evidence=db.search_queries.c.outcome_new_evidence + new_evidence,
                attributed_papers_processed=db.search_queries.c.attributed_papers_processed + (papers_processed * weight),
                attributed_new_interactions=db.search_queries.c.attributed_new_interactions + (new_interactions * weight),
                attributed_new_evidence=db.search_queries.c.attributed_new_evidence + (new_evidence * weight),
                improved_graph=1,
            )
        )

    def get_yield_stats(
        self,
        *,
        query_type: str | None = None,
        generation_reason: str | None = None,
        target_kind: str | None = None,
        limit: int = 50,
    ) -> dict:
        recent = (
            select(db.search_queries)
            .where(db.search_queries.c.status == "done")
            .order_by(db.search_queries.c.completed_at.desc())
            .limit(limit)
        )
        if query_type is not None:
            recent = recent.where(db.search_queries.c.query_type == query_type)
        if generation_reason is not None:
            recent = recent.where(db.search_queries.c.generation_reason == generation_reason)
        if target_kind is not None:
            recent = recent.where(db.search_queries.c.target_kind == target_kind)

        recent_subq = recent.subquery()
        stmt = select(
            func.count().label("n"),
            func.avg(recent_subq.c.outcome_new_evidence).label("avg_new_evidence"),
            func.avg(recent_subq.c.outcome_new_interactions).label("avg_new_interactions"),
            func.avg(recent_subq.c.attributed_new_evidence).label("avg_attributed_new_evidence"),
            func.avg(recent_subq.c.attributed_new_interactions).label("avg_attributed_new_interactions"),
            func.avg(recent_subq.c.papers_new).label("avg_papers_new"),
            func.avg(recent_subq.c.planning_score).label("avg_planning_score"),
            func.avg(recent_subq.c.improved_graph).label("improved_rate"),
        ).select_from(recent_subq)

        row = self.conn.execute(stmt).mappings().one()
        return {
            "n": int(row["n"] or 0),
            "avg_new_evidence": float(row["avg_new_evidence"] or 0.0),
            "avg_new_interactions": float(row["avg_new_interactions"] or 0.0),
            "avg_attributed_new_evidence": float(row["avg_attributed_new_evidence"] or 0.0),
            "avg_attributed_new_interactions": float(row["avg_attributed_new_interactions"] or 0.0),
            "avg_papers_new": float(row["avg_papers_new"] or 0.0),
            "avg_planning_score": float(row["avg_planning_score"] or 0.0),
            "improved_rate": float(row["improved_rate"] or 0.0),
        }


# ---------------------------------------------------------------------------
# Metrics repository
# ---------------------------------------------------------------------------

class MetricsRepo:
    def __init__(self, conn: Connection):
        self.conn = conn

    def log(
        self,
        cycle: int,
        n_entities: int,
        n_interactions: int,
        n_evidence: int,
        n_unresolved_conflicts: int,
        weighted_conflict_sum: float,
        score: float,
        penalty: float,
        papers_processed: int,
        papers_fetched_total: int,
        papers_fetched_new: int,
        papers_extraction_failed: int,
        new_entities: int,
        new_interactions: int,
        new_evidence: int,
        evidence_accepted: int,
        claims_verified: int,
        claims_needing_review: int,
        new_conflicts: int,
        conflicts_reopened: int,
        conflicts_resolved: int,
        queries_generated: int,
        queries_with_new_papers: int,
        query_linked_evidence: int,
    ):
        self.conn.execute(db.metrics_log.insert().values(
            cycle=cycle,
            n_entities=n_entities,
            n_interactions=n_interactions,
            n_evidence=n_evidence,
            n_unresolved_conflicts=n_unresolved_conflicts,
            weighted_conflict_sum=weighted_conflict_sum,
            score=score,
            penalty=penalty,
            papers_processed=papers_processed,
            papers_fetched_total=papers_fetched_total,
            papers_fetched_new=papers_fetched_new,
            papers_extraction_failed=papers_extraction_failed,
            new_entities=new_entities,
            new_interactions=new_interactions,
            new_evidence=new_evidence,
            evidence_accepted=evidence_accepted,
            claims_verified=claims_verified,
            claims_needing_review=claims_needing_review,
            new_conflicts=new_conflicts,
            conflicts_reopened=conflicts_reopened,
            conflicts_resolved=conflicts_resolved,
            queries_generated=queries_generated,
            queries_with_new_papers=queries_with_new_papers,
            query_linked_evidence=query_linked_evidence,
            timestamp=_now(),
        ))

    def get_latest(self) -> Optional[dict]:
        row = self.conn.execute(
            select(db.metrics_log).order_by(db.metrics_log.c.id.desc()).limit(1)
        ).mappings().fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Convenience bundle
# ---------------------------------------------------------------------------

class Repositories:
    """Bundle of all repos sharing a single connection."""
    def __init__(self, conn: Connection):
        self.papers = PaperRepo(conn)
        self.entities = EntityRepo(conn)
        self.interactions = InteractionRepo(conn)
        self.evidence = EvidenceRepo(conn)
        self.literal_claims = LiteralClaimRepo(conn)
        self.conflicts = ConflictRepo(conn)
        self.queries = QueryRepo(conn)
        self.metrics = MetricsRepo(conn)
