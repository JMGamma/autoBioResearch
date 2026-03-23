"""
Conflict resolver (Arm 2b).

- Analyzes open/ambiguous conflicts with LLM to finalize classification
- Generates targeted PubMed/S2 queries to find resolving papers
"""
from __future__ import annotations

import json
import logging

from autobioresearch.config import AppConfig
from autobioresearch.conflict.adjudicator import EvidenceAdjudicator
from autobioresearch.conflict.conflict_prompts import (
    CONFLICT_ANALYSIS_FUNCTION,
    CONFLICT_ANALYSIS_SYSTEM_PROMPT,
    CONFLICT_ANALYSIS_TOOL,
    CONTEXT_ENRICHMENT_FUNCTION,
    CONTEXT_ENRICHMENT_SYSTEM_PROMPT,
    CONTEXT_ENRICHMENT_TOOL,
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
        self._adjudicator = EvidenceAdjudicator()

    def analyze_and_resolve(self, repos: Repositories, config: AppConfig) -> int:
        """
        Use LLM to reclassify AMBIGUOUS conflicts with full context.
        Returns count of conflicts whose status changed.
        """
        open_conflicts = repos.conflicts.get_open(config.conflicts_to_analyze_per_cycle)
        if not open_conflicts:
            return 0

        logger.info(f"Analyzing {len(open_conflicts)} open conflicts")
        changed = 0

        for conflict_row in open_conflicts:
            try:
                updated = self._analyze_conflict(conflict_row, repos)
                if updated:
                    changed += 1
            except Exception as e:
                logger.warning(f"Conflict analysis failed for {conflict_row['id']}: {e}")

        return changed

    def generate_follow_up_queries(self, repos: Repositories, config: AppConfig) -> int:
        """
        Generate targeted follow-up queries for all open conflicts that haven't had
        queries generated yet:
          - true_conflict / ambiguous  → resolution queries (which claim is correct?)
          - context_dependent          → context-enrichment queries (what biology drives
                                         the context-sensitivity?)
        Returns total count of queries added.
        """
        open_conflicts = repos.conflicts.get_open(limit=config.conflicts_to_analyze_per_cycle)
        actionable = [
            c for c in open_conflicts
            if not json.loads(c.get("generated_query_ids") or "[]")
        ]

        if not actionable:
            return 0

        n_resolution = sum(1 for c in actionable if c.get("conflict_type") in ("true_conflict", "ambiguous"))
        n_enrichment = sum(1 for c in actionable if c.get("conflict_type") == "context_dependent")
        logger.info(
            f"Generating follow-up queries for {len(actionable)} conflicts "
            f"({n_resolution} resolution, {n_enrichment} context-enrichment)"
        )

        queries_added = 0
        for conflict_row in actionable:
            ctype = conflict_row.get("conflict_type", "ambiguous")
            if ctype == "context_dependent":
                queries = self._generate_context_enrichment_queries(conflict_row, repos)
                label = "context-enrichment"
            else:
                queries = self._generate_resolution_queries_for_conflict(conflict_row, repos)
                label = "resolution"

            if queries:
                for q in queries:
                    repos.queries.insert(q)
                query_ids = [q.id for q in queries]
                repos.conflicts.add_generated_queries(conflict_row["id"], query_ids)
                repos.conflicts.update_status(conflict_row["id"], "investigating")
                queries_added += len(queries)
                logger.info(
                    f"Conflict {conflict_row['id'][:8]} ({ctype}): {len(queries)} "
                    f"{label} queries queued → investigating"
                )

        if queries_added:
            logger.info(f"Added {queries_added} follow-up queries total")
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

        ev_a = self._refresh_and_load_adjudicated_evidence(repos, int_a["id"])
        ev_b = self._refresh_and_load_adjudicated_evidence(repos, int_b["id"])

        recommendation = self._adjudicator.recommend_conflict_outcome(
            ev_a,
            ev_b,
            current_type=conflict_row.get("conflict_type", "ambiguous"),
        )
        if recommendation is not None:
            self._update_conflict_type(
                repos,
                conflict_row["id"],
                recommendation["conflict_type"],
                float(recommendation["penalty_weight"]),
                recommendation["note"],
            )
            repos.conflicts.update_status(
                conflict_row["id"],
                recommendation["status"],
                note=recommendation["note"],
                resolution_paper_id=recommendation.get("resolution_paper_id"),
                penalty_weight=float(recommendation["penalty_weight"]),
            )
            logger.info(
                f"Conflict {conflict_row['id'][:8]} auto-resolved via adjudication "
                f"as {recommendation['conflict_type']}"
            )
            return True

        if conflict_row.get("conflict_type") != "ambiguous":
            return False

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

        self._update_conflict_type(repos, conflict_row["id"], new_type, new_weight, llm_analysis)

        old_type = conflict_row["conflict_type"]
        changed = old_type != new_type
        logger.info(
            f"Conflict {conflict_row['id'][:8]}: {old_type} -> {new_type} "
            f"(weight={new_weight:.2f})"
            + (f" | reasoning: {llm_analysis[:120]}..." if llm_analysis else "")
        )
        return changed

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

    def _build_conflict_context(self, conflict_row: dict, repos: Repositories):
        """Shared helper: load interactions, evidence, and entity names for a conflict."""
        int_a = repos.interactions.get_by_id(conflict_row["interaction_a_id"])
        int_b = repos.interactions.get_by_id(conflict_row["interaction_b_id"])
        if not int_a or not int_b:
            return None, None, None, None, None, None
        ev_a = self._refresh_and_load_adjudicated_evidence(repos, int_a["id"])
        ev_b = self._refresh_and_load_adjudicated_evidence(repos, int_b["id"])
        entity_a = repos.entities.get_by_id(int_a["entity_a_id"])
        entity_b = repos.entities.get_by_id(int_a["entity_b_id"])
        name_a = entity_a["canonical_name"] if entity_a else "unknown"
        name_b = entity_b["canonical_name"] if entity_b else "unknown"
        return int_a, int_b, ev_a, ev_b, name_a, name_b

    def _refresh_and_load_adjudicated_evidence(self, repos: Repositories, interaction_id: str) -> list[dict]:
        evidence_rows = repos.evidence.get_for_interaction_with_paper_metadata(interaction_id)
        refreshed: list[dict] = []
        for row in evidence_rows:
            score, notes = self._adjudicator.adjudicate(
                row,
                {"year": row.get("paper_year")},
            )
            repos.evidence.update_adjudication(
                row["id"],
                score=score,
                notes=notes,
            )
            updated = dict(row)
            updated["adjudication_score"] = score
            updated["adjudication_notes"] = notes
            refreshed.append(updated)
        return refreshed

    def _evidence_summary_stats(self, evidence_list: list[dict]) -> dict:
        if not evidence_list:
            return {
                "mean_adjudication": 0.0,
                "mean_verification": 0.0,
                "max_adjudication": 0.0,
                "normalized_contexts": [],
            }
        mean_adjudication = sum(float(ev.get("adjudication_score") or 0.0) for ev in evidence_list) / len(evidence_list)
        mean_verification = sum(float(ev.get("verification_score") or 0.0) for ev in evidence_list) / len(evidence_list)
        max_adjudication = max(float(ev.get("adjudication_score") or 0.0) for ev in evidence_list)
        normalized_contexts = sorted({
            "|".join(filter(None, [
                ev.get("normalized_organism") or ev.get("organism"),
                ev.get("normalized_tissue_cell_type") or ev.get("tissue_cell_type"),
                ev.get("normalized_condition") or ev.get("condition"),
            ]))
            for ev in evidence_list
            if any([
                ev.get("normalized_organism") or ev.get("organism"),
                ev.get("normalized_tissue_cell_type") or ev.get("tissue_cell_type"),
                ev.get("normalized_condition") or ev.get("condition"),
            ])
        })
        return {
            "mean_adjudication": round(mean_adjudication, 4),
            "mean_verification": round(mean_verification, 4),
            "max_adjudication": round(max_adjudication, 4),
            "normalized_contexts": normalized_contexts,
        }

    def _generate_resolution_queries_for_conflict(
        self, conflict_row: dict, repos: Repositories
    ) -> list[SearchQuery]:
        """Resolution queries for true_conflict / ambiguous: find papers that adjudicate."""
        int_a, int_b, ev_a, ev_b, name_a, name_b = self._build_conflict_context(conflict_row, repos)
        if int_a is None:
            return []

        user_prompt = (
            f"Generate search queries to investigate this biological conflict.\n"
            f"Consider BOTH possibilities: (1) a biological context that explains why "
            f"both results are valid, or (2) papers that adjudicate which claim is correct.\n\n"
            f"ENTITIES: {name_a} and {name_b}\n"
            f"INTERACTION TYPE: {int_a['interaction_type']}\n"
            f"CONFLICT AXIS: {conflict_row.get('conflict_axis', 'effect')}\n\n"
            f"CLAIM A: effect='{int_a.get('effect')}', direction='{int_a.get('direction')}'\n"
            f"Evidence A: {self._summarize_evidence(ev_a)}\n\n"
            f"CLAIM B: effect='{int_b.get('effect')}', direction='{int_b.get('direction')}'\n"
            f"Evidence B: {self._summarize_evidence(ev_b)}\n\n"
            f"Known context differences (if any): {conflict_row.get('context_difference', '{}')}\n"
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

    def _generate_context_enrichment_queries(
        self, conflict_row: dict, repos: Repositories
    ) -> list[SearchQuery]:
        """
        Context-enrichment queries for context_dependent conflicts.
        Goal: find papers that explain the MECHANISM behind the context-sensitivity —
        not to invalidate either claim but to understand why the biology differs.
        """
        int_a, int_b, ev_a, ev_b, name_a, name_b = self._build_conflict_context(conflict_row, repos)
        if int_a is None:
            return []

        context_diff = conflict_row.get("context_difference", "{}")

        user_prompt = (
            f"Two papers report the same interaction with different outcomes depending on context.\n\n"
            f"ENTITIES: {name_a} and {name_b}\n"
            f"INTERACTION TYPE: {int_a['interaction_type']}\n\n"
            f"CLAIM A: effect='{int_a.get('effect')}', direction='{int_a.get('direction')}'\n"
            f"Context A: {self._summarize_evidence(ev_a)}\n\n"
            f"CLAIM B: effect='{int_b.get('effect')}', direction='{int_b.get('direction')}'\n"
            f"Context B: {self._summarize_evidence(ev_b)}\n\n"
            f"Known context differences: {context_diff}\n"
            f"Conflict axis: {conflict_row.get('conflict_axis', 'effect')}\n\n"
            f"Generate queries to find papers that explain WHY this interaction behaves "
            f"differently across these contexts — the mechanistic basis for the context-sensitivity. "
            f"Do NOT try to determine which claim is 'correct'; both are valid in their contexts."
        )

        result = self._llm.call_with_tool(
            system=CONTEXT_ENRICHMENT_SYSTEM_PROMPT,
            user=user_prompt,
            tool=CONTEXT_ENRICHMENT_TOOL,
            tool_function=CONTEXT_ENRICHMENT_FUNCTION,
        )

        if not result:
            return []

        queries: list[SearchQuery] = []
        for q_data in result.get("queries", []):
            q = SearchQuery(
                query_text=q_data.get("query_text", ""),
                source_api=q_data.get("source_api", "pubmed"),
                query_type=QueryType.CONFLICT_RESOLUTION,  # reuse type; origin distinguishes it
                origin=f"context_enrichment:{conflict_row['id']}",
            )
            if q.query_text:
                queries.append(q)

        return queries

    def _build_conflict_prompt(
        self, int_a: dict, int_b: dict, ev_a: list[dict], ev_b: list[dict]
    ) -> str:
        stats_a = self._evidence_summary_stats(ev_a)
        stats_b = self._evidence_summary_stats(ev_b)
        return (
            f"Analyze whether these two interaction claims genuinely conflict:\n\n"
            f"CLAIM A:\n"
            f"  Interaction type: {int_a['interaction_type']}\n"
            f"  Direction: {int_a.get('direction')}\n"
            f"  Effect: {int_a.get('effect')}\n"
            f"  Composite confidence: {int_a.get('composite_confidence')} "
            f"({int_a.get('composite_confidence_score', 0):.2f})\n"
            f"  Adjudication summary: mean={stats_a['mean_adjudication']:.2f}, "
            f"max={stats_a['max_adjudication']:.2f}, verification={stats_a['mean_verification']:.2f}\n"
            f"  Normalized contexts: {stats_a['normalized_contexts']}\n"
            f"  Evidence ({len(ev_a)} records):\n"
            f"  {self._summarize_evidence(ev_a)}\n\n"
            f"CLAIM B:\n"
            f"  Interaction type: {int_b['interaction_type']}\n"
            f"  Direction: {int_b.get('direction')}\n"
            f"  Effect: {int_b.get('effect')}\n"
            f"  Composite confidence: {int_b.get('composite_confidence')} "
            f"({int_b.get('composite_confidence_score', 0):.2f})\n"
            f"  Adjudication summary: mean={stats_b['mean_adjudication']:.2f}, "
            f"max={stats_b['max_adjudication']:.2f}, verification={stats_b['mean_verification']:.2f}\n"
            f"  Normalized contexts: {stats_b['normalized_contexts']}\n"
            f"  Evidence ({len(ev_b)} records):\n"
            f"  {self._summarize_evidence(ev_b)}\n\n"
            f"When reasoning, compare evidence quality, recency, assay directness, "
            f"and consistency across normalized contexts.\n\n"
            f"Use the classify_conflict tool to analyze this."
        )

    def _summarize_evidence(self, evidence_list: list[dict]) -> str:
        if not evidence_list:
            return "  (no evidence records)"
        lines = []
        for ev in evidence_list[:5]:  # limit to avoid token explosion
            lines.append(
                f"    - [{ev.get('evidence_type')}] "
                f"organism={ev.get('normalized_organism') or ev.get('organism')} "
                f"tissue={ev.get('normalized_tissue_cell_type') or ev.get('tissue_cell_type')} "
                f"condition={ev.get('normalized_condition') or ev.get('condition')} "
                f"assay={ev.get('normalized_assay_type') or ev.get('assay_type')} "
                f"confidence={ev.get('confidence_score', 0):.2f} "
                f"verification={ev.get('verification_score', 0) or 0:.2f} "
                f"adjudication={ev.get('adjudication_score', 0) or 0:.2f} "
                f"year={ev.get('paper_year')} "
                f"snippet=\"{(ev.get('snippet') or '')[:120]}...\""
            )
        return "\n".join(lines)
