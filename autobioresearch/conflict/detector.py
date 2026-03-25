"""
Conflict detector (Arm 2a).

Scans the interactions table for pairs that make opposite claims about
the same entity pair + interaction type, then classifies each conflict:
  1. Rule-based pre-filter (no LLM cost)
  2. LLM analysis for ambiguous cases
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from autobioresearch.config import AppConfig
from autobioresearch.models import Conflict, ConflictType, PENALTY_WEIGHTS
from autobioresearch.storage.repositories import Repositories

logger = logging.getLogger(__name__)


class ConflictDetector:
    def detect(self, repos: Repositories, config: AppConfig) -> int:
        """
        Find new conflicts and insert them into the DB.
        Returns count of new conflicts found.
        """
        candidate_pairs = repos.interactions.get_pairs_for_conflict_detection(
            limit=config.max_candidate_pairs_per_cycle
        )

        if not candidate_pairs:
            return 0

        logger.info(f"Evaluating {len(candidate_pairs)} candidate conflict pairs")
        new_conflicts = 0

        for int_a, int_b in candidate_pairs:
            conflict = self._classify_rule_based(int_a, int_b, repos)
            if conflict:
                repos.conflicts.insert(conflict)
                new_conflicts += 1

        if new_conflicts:
            logger.info(f"Found {new_conflicts} new conflicts")
        return new_conflicts

    # ------------------------------------------------------------------
    # Rule-based classification (no LLM)
    # ------------------------------------------------------------------

    def _classify_rule_based(
        self,
        int_a: dict,
        int_b: dict,
        repos: Repositories,
    ) -> Optional[Conflict]:
        """
        Classify a candidate conflict pair without LLM.
        Returns a Conflict or None (if it's clearly not a conflict).
        """
        # Get evidence summaries for both interactions
        ev_a = repos.evidence.get_for_interaction(int_a["id"])
        ev_b = repos.evidence.get_for_interaction(int_b["id"])

        # Collect context attributes from evidence
        orgs_a = {e.get("normalized_organism") or e.get("organism") for e in ev_a if (e.get("normalized_organism") or e.get("organism"))}
        orgs_b = {e.get("normalized_organism") or e.get("organism") for e in ev_b if (e.get("normalized_organism") or e.get("organism"))}
        tissues_a = {e.get("normalized_tissue_cell_type") or e.get("tissue_cell_type") for e in ev_a if (e.get("normalized_tissue_cell_type") or e.get("tissue_cell_type"))}
        tissues_b = {e.get("normalized_tissue_cell_type") or e.get("tissue_cell_type") for e in ev_b if (e.get("normalized_tissue_cell_type") or e.get("tissue_cell_type"))}
        conditions_a = {e.get("normalized_condition") or e.get("condition") for e in ev_a if (e.get("normalized_condition") or e.get("condition"))}
        conditions_b = {e.get("normalized_condition") or e.get("condition") for e in ev_b if (e.get("normalized_condition") or e.get("condition"))}

        context_diff: dict = {}
        conflict_axis = "effect"

        effect_differs = (
            int_a.get("effect") is not None
            and int_b.get("effect") is not None
            and int_a.get("effect") != int_b.get("effect")
        )
        direction_differs = (
            int_a.get("direction") != int_b.get("direction")
            and "undirected" not in (int_a.get("direction", ""), int_b.get("direction", ""))
        )

        # Prefer effect conflicts when both dimensions differ; otherwise surface direction-only disputes.
        if direction_differs and not effect_differs:
            conflict_axis = "direction"

        # Check organism overlap
        org_overlap = orgs_a & orgs_b
        org_diff = orgs_a.symmetric_difference(orgs_b)

        if org_diff and not org_overlap:
            # Completely different organisms → context-dependent, low penalty
            context_diff["organism"] = f"{sorted(orgs_a)} vs {sorted(orgs_b)}"
            return Conflict(
                interaction_a_id=int_a["id"],
                interaction_b_id=int_b["id"],
                conflict_type=ConflictType.CONTEXT_DEPENDENT,
                conflict_axis=conflict_axis,
                context_difference=context_diff,
                penalty_weight=PENALTY_WEIGHTS[ConflictType.CONTEXT_DEPENDENT],
            )

        # Same or overlapping organisms → check tissue/condition
        tissue_diff = tissues_a.symmetric_difference(tissues_b)
        condition_diff = conditions_a.symmetric_difference(conditions_b)

        if tissue_diff:
            context_diff["tissue_cell_type"] = f"{sorted(tissues_a)} vs {sorted(tissues_b)}"
        if condition_diff:
            context_diff["condition"] = f"{sorted(conditions_a)} vs {sorted(conditions_b)}"

        if context_diff and not (tissues_a & tissues_b) and not (conditions_a & conditions_b):
            # No overlapping tissue/condition → likely context-dependent
            return Conflict(
                interaction_a_id=int_a["id"],
                interaction_b_id=int_b["id"],
                conflict_type=ConflictType.CONTEXT_DEPENDENT,
                conflict_axis=conflict_axis,
                context_difference=context_diff,
                penalty_weight=PENALTY_WEIGHTS[ConflictType.CONTEXT_DEPENDENT],
            )

        # Same context, opposite effects → potential true conflict (needs LLM, record as ambiguous for now)
        return Conflict(
            interaction_a_id=int_a["id"],
            interaction_b_id=int_b["id"],
            conflict_type=ConflictType.AMBIGUOUS,
            conflict_axis=conflict_axis,
            context_difference=context_diff,
            penalty_weight=PENALTY_WEIGHTS[ConflictType.AMBIGUOUS],
        )
