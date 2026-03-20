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

from autobioresearch import database as db
from autobioresearch.models import (
    BiologicalEntity,
    Conflict,
    ConflictStatus,
    EvidenceRecord,
    Interaction,
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
            # Update query_ids to include any new query references
            if paper.query_ids:
                row = self.conn.execute(
                    select(db.papers.c.query_ids).where(db.papers.c.id == paper.id)
                ).fetchone()
                existing_ids = json.loads(row.query_ids or "[]")
                merged = list(set(existing_ids + paper.query_ids))
                self.conn.execute(
                    update(db.papers)
                    .where(db.papers.c.id == paper.id)
                    .values(query_ids=json.dumps(merged), updated_at=_now())
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
            .limit(limit)
        ).mappings().all()
        return [dict(r) for r in rows]

    def get_full_text_candidates(self, limit: int = 20) -> list[dict]:
        """Papers that have a pmc_id but haven't had full text fetched yet."""
        rows = self.conn.execute(
            select(db.papers)
            .where(db.papers.c.pmc_id.isnot(None))
            .where(db.papers.c.fetch_status == "abstract_only")
            .where(db.papers.c.extraction_status == "pending")
            .limit(limit)
        ).mappings().all()
        return [dict(r) for r in rows]

    def mark_extraction_done(self, paper_id: str, raw_llm_response: Optional[str] = None):
        self.conn.execute(
            update(db.papers)
            .where(db.papers.c.id == paper_id)
            .values(
                extraction_status="done",
                raw_llm_response=raw_llm_response,
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

    def mark_fetch_status(self, paper_id: str, status: str):
        self.conn.execute(
            update(db.papers)
            .where(db.papers.c.id == paper_id)
            .values(fetch_status=status, updated_at=_now())
        )

    def count(self) -> int:
        return self.conn.execute(select(func.count()).select_from(db.papers)).scalar_one()

    def count_by_extraction_status(self, status: str) -> int:
        return self.conn.execute(
            select(func.count())
            .select_from(db.papers)
            .where(db.papers.c.extraction_status == status)
        ).scalar_one()


# ---------------------------------------------------------------------------
# Entity repository
# ---------------------------------------------------------------------------

class EntityRepo:
    def __init__(self, conn: Connection):
        self.conn = conn

    def insert(self, entity: BiologicalEntity) -> str:
        """Insert a new entity. Returns its id."""
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

    def find_by_synonym(self, synonym: str) -> Optional[str]:
        """Returns entity_id or None."""
        row = self.conn.execute(
            select(db.entity_synonyms.c.entity_id)
            .where(db.entity_synonyms.c.synonym == synonym.lower().strip())
        ).fetchone()
        return row.entity_id if row else None

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
        subq = (
            select(db.interactions.c.entity_a_id, func.count().label("cnt"))
            .group_by(db.interactions.c.entity_a_id)
            .subquery()
        )
        rows = self.conn.execute(
            select(db.entities)
            .outerjoin(subq, db.entities.c.id == subq.c.entity_a_id)
            .where(func.coalesce(subq.c.cnt, 0) < min_interactions)
            .limit(limit)
        ).mappings().all()
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
        Upsert based on (entity_a_id, entity_b_id, interaction_type, effect).
        Returns (interaction_id, is_new).
        """
        row = self.conn.execute(
            select(db.interactions.c.id)
            .where(db.interactions.c.entity_a_id == interaction.entity_a_id)
            .where(db.interactions.c.entity_b_id == interaction.entity_b_id)
            .where(db.interactions.c.interaction_type == interaction.interaction_type.value)
            .where(db.interactions.c.effect == interaction.effect)
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
        Return pairs of interactions on the same entity pair + type with different effects.
        Excludes pairs already in the conflicts table.
        """
        stmt = text("""
            SELECT a.id as a_id, b.id as b_id
            FROM interactions a
            JOIN interactions b ON (
                (a.entity_a_id = b.entity_a_id AND a.entity_b_id = b.entity_b_id)
                OR (a.entity_a_id = b.entity_b_id AND a.entity_b_id = b.entity_a_id)
            )
            WHERE a.interaction_type = b.interaction_type
              AND a.effect IS NOT NULL
              AND b.effect IS NOT NULL
              AND a.effect != b.effect
              AND a.id < b.id
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
            snippet=ev.snippet[:400],
            snippet_start=ev.snippet_start,
            snippet_end=ev.snippet_end,
            created_at=_now(),
        ))
        return True

    def get_for_interaction(self, interaction_id: str) -> list[dict]:
        rows = self.conn.execute(
            select(db.evidence).where(db.evidence.c.interaction_id == interaction_id)
        ).mappings().all()
        return [dict(r) for r in rows]

    def count(self) -> int:
        return self.conn.execute(select(func.count()).select_from(db.evidence)).scalar_one()


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
            .where(db.conflicts.c.status == "open")
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
            .where(db.conflicts.c.status == "open")
        ).scalar_one()

    def weighted_sum_open(self) -> float:
        """Sum of penalty_weight for all open conflicts."""
        result = self.conn.execute(
            select(func.sum(db.conflicts.c.penalty_weight))
            .where(db.conflicts.c.status == "open")
        ).scalar()
        return float(result or 0.0)

    def count(self) -> int:
        return self.conn.execute(select(func.count()).select_from(db.conflicts)).scalar_one()

    def count_resolved(self) -> int:
        return self.conn.execute(
            select(func.count())
            .select_from(db.conflicts)
            .where(db.conflicts.c.status == "resolved")
        ).scalar_one()


# ---------------------------------------------------------------------------
# Query repository
# ---------------------------------------------------------------------------

class QueryRepo:
    def __init__(self, conn: Connection):
        self.conn = conn

    def insert(self, query: SearchQuery):
        self.conn.execute(db.search_queries.insert().values(
            id=query.id,
            query_text=query.query_text,
            source_api=query.source_api,
            query_type=query.query_type.value,
            origin=query.origin,
            status=query.status,
            papers_found=query.papers_found,
            papers_new=query.papers_new,
            created_at=_now(),
            executed_at=query.executed_at,
        ))

    def insert_many(self, queries: list[SearchQuery]):
        for q in queries:
            self.insert(q)

    def get_pending(self, limit: int = 10) -> list[dict]:
        rows = self.conn.execute(
            select(db.search_queries)
            .where(db.search_queries.c.status == "pending")
            .order_by(db.search_queries.c.created_at)
            .limit(limit)
        ).mappings().all()
        return [dict(r) for r in rows]

    def mark_running(self, query_id: str):
        self.conn.execute(
            update(db.search_queries)
            .where(db.search_queries.c.id == query_id)
            .values(status="running", executed_at=_now())
        )

    def mark_done(self, query_id: str, papers_found: int, papers_new: int):
        self.conn.execute(
            update(db.search_queries)
            .where(db.search_queries.c.id == query_id)
            .values(status="done", papers_found=papers_found, papers_new=papers_new)
        )

    def mark_failed(self, query_id: str):
        self.conn.execute(
            update(db.search_queries)
            .where(db.search_queries.c.id == query_id)
            .values(status="failed")
        )

    def count_pending(self) -> int:
        return self.conn.execute(
            select(func.count())
            .select_from(db.search_queries)
            .where(db.search_queries.c.status == "pending")
        ).scalar_one()


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
        new_entities: int,
        new_interactions: int,
        new_evidence: int,
        new_conflicts: int,
        conflicts_resolved: int,
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
            new_entities=new_entities,
            new_interactions=new_interactions,
            new_evidence=new_evidence,
            new_conflicts=new_conflicts,
            conflicts_resolved=conflicts_resolved,
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
        self.conflicts = ConflictRepo(conn)
        self.queries = QueryRepo(conn)
        self.metrics = MetricsRepo(conn)
