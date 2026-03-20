"""
Conflict resolver (Arm 2b).

- Analyzes open/ambiguous conflicts with LLM to finalize classification
- Generates targeted PubMed/S2 queries to find resolving papers
"""
from __future__ import annotations

import json
import logging

from autobioresearch.config import AppConfig
from autobioresearch.conflict.conflict_prompts import (
    CONFLICT_ANALYSIS_FUNCTION,
    CONFLICT_ANALYSIS_SYSTEM_PROMPT,
    CONFLICT_ANALYSIS_TOOL,
    QUERY_GENERATION_FUNCTION,
    QUERY_GENERATION_SYSTEM_PROMPT,
    QUERY_GENERATION_TOOL,
)
from autobioresearch.extractor.claude_client import LLMClient
from autobioresearch.models import ConflictType, PENALTY_WEIGHTS, QueryType, SearchQuery
from autobioresearch.storage.repositories import Repositories

logger = logging.getLogger(__name__)


class ConflictResolver:
    def __init__(self, llm: LLMClient):
        self._llm = llm

    def analyze_and_resolve(self, repos: Repositories, config: AppConfig) -> int:
        """
        Use LLM to reclassify AMBIGUOUS conflicts with full context.
        Returns count of conflicts whose status changed.
        """
        open_conflicts = repos.conflicts.get_open(config.conflicts_to_analyze_per_cycle)
        # Only send ambiguous ones to LLM (true/context-dep already classified)
        ambiguous = [c for c in open_conflicts if c.get("conflict_type") == "ambiguous"]

        if not ambiguous:
            return 0

        logger.info(f"LLM analyzing {len(ambiguous)} ambiguous conflicts")
        changed = 0

        for conflict_row in ambiguous:
            try:
                updated = self._analyze_conflict(conflict_row, repos)
                if updated:
                    changed += 1
            except Exception as e:
                logger.warning(f"Conflict analysis failed for {conflict_row['id']}: {e}")

        return changed

    def generate_resolution_queries(self, repos: Repositories, config: AppConfig) -> int:
        """
        Generate search queries for open TRUE_CONFLICT conflicts.
        Returns count of queries added.
        """
        open_conflicts = repos.conflicts.get_open(limit=config.conflicts_to_analyze_per_cycle)
        true_conflicts = [
            c for c in open_conflicts
            if c.get("conflict_type") == "true_conflict"
            and not json.loads(c.get("generated_query_ids") or "[]")
        ]

        if not true_conflicts:
            return 0

        logger.info(f"Generating resolution queries for {len(true_conflicts)} true conflicts")
        queries_added = 0

        for conflict_row in true_conflicts:
            queries = self._generate_queries_for_conflict(conflict_row, repos)
            if queries:
                for q in queries:
                    repos.queries.insert(q)
                query_ids = [q.id for q in queries]
                repos.conflicts.add_generated_queries(conflict_row["id"], query_ids)
                repos.conflicts.update_status(conflict_row["id"], "investigating")
                queries_added += len(queries)

        if queries_added:
            logger.info(f"Added {queries_added} conflict-resolution queries")
        return queries_added

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _analyze_conflict(self, conflict_row: dict, repos: Repositories) -> bool:
        """LLM analysis of a single conflict. Returns True if classification changed."""
        int_a = repos.interactions.get_by_id(conflict_row["interaction_a_id"])
        int_b = repos.interactions.get_by_id(conflict_row["interaction_b_id"])
        if not int_a or not int_b:
            return False

        ev_a = repos.evidence.get_for_interaction(int_a["id"])
        ev_b = repos.evidence.get_for_interaction(int_b["id"])

        user_prompt = self._build_conflict_prompt(int_a, int_b, ev_a, ev_b)

        result = self._llm.call_with_tool(
            system=CONFLICT_ANALYSIS_SYSTEM_PROMPT,
            user=user_prompt,
            tool=CONFLICT_ANALYSIS_TOOL,
            tool_function=CONFLICT_ANALYSIS_FUNCTION,
        )

        if not result:
            return False

        new_type = result.get("conflict_type", "ambiguous")
        new_weight = float(result.get("penalty_weight", 0.5))
        llm_analysis = result.get("reasoning", "")

        repos.conflicts.update_status(
            conflict_row["id"],
            status="open",
            llm_analysis=llm_analysis,
            penalty_weight=new_weight,
        )

        # Update conflict_type in DB (update_status doesn't do this, so use direct update)
        from sqlalchemy import update as sa_update
        from autobioresearch import database as db
        from autobioresearch.storage.repositories import _now
        # Access via repos conn
        repos.conflicts._ConflictRepo__get_conn_and_update(
            conflict_row["id"], new_type, new_weight, llm_analysis
        ) if hasattr(repos.conflicts, '_ConflictRepo__get_conn_and_update') else None

        # Simpler: just call the correct update path
        self._update_conflict_type(repos, conflict_row["id"], new_type, new_weight, llm_analysis)

        logger.debug(f"Conflict {conflict_row['id']}: {conflict_row['conflict_type']} -> {new_type} (weight={new_weight:.2f})")
        return conflict_row["conflict_type"] != new_type

    def _update_conflict_type(
        self,
        repos: Repositories,
        conflict_id: str,
        conflict_type: str,
        penalty_weight: float,
        llm_analysis: str,
    ):
        """Direct SQL update to change conflict_type and penalty_weight."""
        from autobioresearch import database as db
        from autobioresearch.storage.repositories import _now
        from sqlalchemy import update as sa_update

        repos.conflicts.conn.execute(
            sa_update(db.conflicts)
            .where(db.conflicts.c.id == conflict_id)
            .values(
                conflict_type=conflict_type,
                penalty_weight=penalty_weight,
                llm_analysis=llm_analysis[:5000] if llm_analysis else None,
                updated_at=_now(),
            )
        )

    def _generate_queries_for_conflict(
        self, conflict_row: dict, repos: Repositories
    ) -> list[SearchQuery]:
        int_a = repos.interactions.get_by_id(conflict_row["interaction_a_id"])
        int_b = repos.interactions.get_by_id(conflict_row["interaction_b_id"])
        if not int_a or not int_b:
            return []

        ev_a = repos.evidence.get_for_interaction(int_a["id"])
        ev_b = repos.evidence.get_for_interaction(int_b["id"])

        entity_a = repos.entities.get_by_id(int_a["entity_a_id"])
        entity_b = repos.entities.get_by_id(int_a["entity_b_id"])

        name_a = entity_a["canonical_name"] if entity_a else "unknown"
        name_b = entity_b["canonical_name"] if entity_b else "unknown"

        user_prompt = (
            f"Generate search queries to resolve this biological conflict:\n\n"
            f"ENTITIES: {name_a} and {name_b}\n"
            f"INTERACTION TYPE: {int_a['interaction_type']}\n\n"
            f"CLAIM A: effect='{int_a.get('effect')}', direction='{int_a.get('direction')}'\n"
            f"Evidence: {self._summarize_evidence(ev_a)}\n\n"
            f"CLAIM B: effect='{int_b.get('effect')}', direction='{int_b.get('direction')}'\n"
            f"Evidence: {self._summarize_evidence(ev_b)}\n\n"
            f"Context difference: {conflict_row.get('context_difference', '{}')}\n"
            f"Conflict axis: {conflict_row.get('conflict_axis', 'effect')}\n"
        )

        result = self._llm.call_with_tool(
            system=QUERY_GENERATION_SYSTEM_PROMPT,
            user=user_prompt,
            tool=QUERY_GENERATION_TOOL,
            tool_function=QUERY_GENERATION_FUNCTION,
        )

        if not result:
            return []

        queries: list[SearchQuery] = []
        for q_data in result.get("queries", []):
            q = SearchQuery(
                query_text=q_data.get("query_text", ""),
                source_api=q_data.get("source_api", "pubmed"),
                query_type=QueryType.CONFLICT_RESOLUTION,
                origin=f"conflict_id:{conflict_row['id']}",
            )
            if q.query_text:
                queries.append(q)

        return queries

    def _build_conflict_prompt(
        self, int_a: dict, int_b: dict, ev_a: list[dict], ev_b: list[dict]
    ) -> str:
        return (
            f"Analyze whether these two interaction claims genuinely conflict:\n\n"
            f"CLAIM A:\n"
            f"  Interaction type: {int_a['interaction_type']}\n"
            f"  Direction: {int_a.get('direction')}\n"
            f"  Effect: {int_a.get('effect')}\n"
            f"  Composite confidence: {int_a.get('composite_confidence')} "
            f"({int_a.get('composite_confidence_score', 0):.2f})\n"
            f"  Evidence ({len(ev_a)} records):\n"
            f"  {self._summarize_evidence(ev_a)}\n\n"
            f"CLAIM B:\n"
            f"  Interaction type: {int_b['interaction_type']}\n"
            f"  Direction: {int_b.get('direction')}\n"
            f"  Effect: {int_b.get('effect')}\n"
            f"  Composite confidence: {int_b.get('composite_confidence')} "
            f"({int_b.get('composite_confidence_score', 0):.2f})\n"
            f"  Evidence ({len(ev_b)} records):\n"
            f"  {self._summarize_evidence(ev_b)}\n\n"
            f"Use the classify_conflict tool to analyze this."
        )

    def _summarize_evidence(self, evidence_list: list[dict]) -> str:
        if not evidence_list:
            return "  (no evidence records)"
        lines = []
        for ev in evidence_list[:5]:  # limit to avoid token explosion
            lines.append(
                f"    - [{ev.get('evidence_type')}] "
                f"organism={ev.get('organism')} "
                f"tissue={ev.get('tissue_cell_type')} "
                f"condition={ev.get('condition')} "
                f"confidence={ev.get('confidence_score', 0):.2f} "
                f"snippet=\"{(ev.get('snippet') or '')[:120]}...\""
            )
        return "\n".join(lines)
