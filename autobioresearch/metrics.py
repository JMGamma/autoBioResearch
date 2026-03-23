"""
Score computation for the autoresearch loop.

score = (N_entities × N_interactions) / (1 + penalty × weighted_conflict_sum)

weighted_conflict_sum = sum of penalty_weight over all open conflicts
  true_conflict=1.0, ambiguous=0.5, context_dependent=0.2

Higher score = larger, less-conflicted knowledge base.
"""
from __future__ import annotations

import logging

from autobioresearch.config import AppConfig
from autobioresearch.storage.repositories import Repositories

logger = logging.getLogger(__name__)


def compute_score(repos: Repositories, config: AppConfig) -> tuple[float, dict]:
    """
    Returns (score, stats_dict).
    stats_dict contains the raw counts used in the calculation.
    """
    n_entities = repos.entities.count()
    n_interactions = repos.interactions.count()
    n_evidence = repos.evidence.count()
    n_open_conflicts = repos.conflicts.count_unresolved()
    weighted_sum = repos.conflicts.weighted_sum_unresolved()

    score = (n_entities * n_interactions) / (1.0 + config.penalty * weighted_sum)

    stats = {
        "n_entities": n_entities,
        "n_interactions": n_interactions,
        "n_evidence": n_evidence,
        "n_unresolved_conflicts": n_open_conflicts,
        "weighted_conflict_sum": weighted_sum,
        "score": score,
        "penalty": config.penalty,
    }

    logger.info(
        f"Score: {score:.2f} | "
        f"Entities: {n_entities} | "
        f"Interactions: {n_interactions} | "
        f"Evidence: {n_evidence} | "
        f"Open conflicts: {n_open_conflicts} (weighted: {weighted_sum:.1f})"
    )

    return score, stats
