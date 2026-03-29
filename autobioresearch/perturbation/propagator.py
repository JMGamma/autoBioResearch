"""
BFS perturbation propagation engine.

Shared between the Phase 0 prototype script and the Phase 1 FastAPI endpoint.
Do not add HTTP or CLI logic here.

ASCII diagram of propagation:

  seed (score = ±1.0)
    │
    ├─[activates, conf=0.8]──▶ entity_B  (score = seed × +1 × 0.8)
    │
    └─[inhibits,  conf=0.6]──▶ entity_C  (score = seed × -1 × 0.6)
                                   │
                                   └─[activates, conf=0.7]──▶ entity_D (score = entity_C_score × +1 × 0.7)

Multi-path combination when entity_D is reached via two paths:
  combined = pos_combined - neg_combined
  where each group uses: 1 - ∏(1 - min(|xᵢ|,1)^α), α = COMBINATION_EXPONENT
"""

from __future__ import annotations

import math
from collections import defaultdict, deque

from sqlalchemy import text

from autobioresearch.perturbation.sign_map import effect_to_sign

# Combination exponent α used in the multi-path score formula:
#   combined = 1 - ∏(1 - min(|xᵢ|, 1)^α)
#
# α controls how quickly redundant paths saturate the combined score:
#   α → 1.0  linear combination — each additional path contributes nearly its full weight
#   α → 0.0  rapid saturation — the first strong path dominates; extra paths add little
#
# 0.55 was chosen empirically to give sub-linear but not extreme diminishing returns:
# two equally-weighted paths of 0.5 combine to ~0.69 (vs 0.75 at α=1, 0.5 at α→0).
# Raise toward 1.0 if you want more additive multi-path reinforcement; lower toward
# 0.3 if you want a single dominant path to suppress noisy secondary routes.
_DEFAULT_COMBINATION_EXPONENT: float = 0.55  # fallback when config is unavailable

_OUTGOING_EDGES_SQL = text("""
    SELECT i.id                                          AS interaction_id,
           i.entity_b_id                                AS target_id,
           e.canonical_name                             AS target_name,
           i.effect,
           COALESCE(i.composite_confidence_score, 0.0)  AS confidence_score
    FROM   interactions i
    JOIN   entities     e ON i.entity_b_id = e.id
    WHERE  i.entity_a_id = :entity_id
      AND  UPPER(i.direction) IN ('A_TO_B', 'BIDIRECTIONAL')

    UNION ALL

    SELECT i.id                                          AS interaction_id,
           i.entity_a_id                                AS target_id,
           e.canonical_name                             AS target_name,
           i.effect,
           COALESCE(i.composite_confidence_score, 0.0)  AS confidence_score
    FROM   interactions i
    JOIN   entities     e ON i.entity_a_id = e.id
    WHERE  i.entity_b_id = :entity_id
      AND  UPPER(i.direction) IN ('B_TO_A', 'BIDIRECTIONAL')
""")


def get_outgoing_edges(conn, entity_id: str) -> list[dict]:
    """
    Return all outgoing directed edges from entity_id as a list of dicts.

    Keys: interaction_id, target_id, target_name, effect, confidence_score.
    Uses UPPER() to tolerate mixed-case direction values in the DB.
    NULL composite_confidence_score is coerced to 0.0 via SQL COALESCE.
    """
    rows = conn.execute(_OUTGOING_EDGES_SQL, {"entity_id": entity_id}).mappings().all()
    return [dict(r) for r in rows]


def combine_scores(
    path_scores: list[float],
    exponent: float = _DEFAULT_COMBINATION_EXPONENT,
) -> float:
    """
    Combine multiple same-sign path scores using the independence formula:

        combined = 1 - ∏(1 - min(|xᵢ|, 1.0)^α)

    where α = exponent (default 0.55, configurable via perturbation_combination_exponent).

    - Empty list → 0.0
    - Clamps |xᵢ| to 1.0 before the power to prevent complex numbers from
      floating-point drift above 1.0.
    """
    if not path_scores:
        return 0.0
    product = 1.0
    for x in path_scores:
        clamped = min(abs(x), 1.0)
        product *= 1.0 - (clamped ** exponent)
    return 1.0 - product


def run_propagation(
    conn,
    seed_entity_id: str,
    seed_name: str,
    mode: str,
    depth: int,
    exponent: float = _DEFAULT_COMBINATION_EXPONENT,
) -> dict:
    """
    BFS perturbation propagation from seed_entity_id.

    Args:
        conn:            SQLAlchemy connection (read-only).
        seed_entity_id:  UUID of the seed entity.
        seed_name:       Canonical name of the seed entity.
        mode:            'suppress' (seed score = -1.0) or 'promote' (seed score = +1.0).
        depth:           Number of hops to propagate (0 = seed only).

    Returns dict with keys: seed, affected, excluded_edges, stats.
    The caller wraps this with schema_version and query metadata.
    """
    seed_score = -1.0 if mode == "suppress" else 1.0

    # depth=0: return seed only, no propagation
    if depth == 0:
        return {
            "seed": {"entity_id": seed_entity_id, "name": seed_name, "perturbation_score": seed_score},
            "affected": [],
            "excluded_edges": [],
            "stats": {"n_affected": 0, "n_excluded_edges": 0, "duration_ms": 0},
        }

    # visited: entities whose outgoing edges have been (or are queued to be) expanded.
    # Initialized with seed to prevent back-propagation into the seed.
    visited: set[str] = {seed_entity_id}

    # Accumulate all path scores per entity (may arrive via multiple routes).
    # paths_to_entity[entity_id] = [(score, hop_depth, contributing_interaction_dict), ...]
    paths_to_entity: dict[str, list[tuple[float, int, dict]]] = defaultdict(list)
    entity_names: dict[str, str] = {}
    excluded_edges: list[dict] = []

    # BFS frontier: (entity_id, entity_name, score_at_this_node, hop_depth)
    frontier: deque[tuple[str, str, float, int]] = deque(
        [(seed_entity_id, seed_name, seed_score, 0)]
    )

    while frontier:
        node_id, node_name, node_score, node_depth = frontier.popleft()

        if node_depth >= depth:
            continue  # don't expand past requested depth

        for edge in get_outgoing_edges(conn, node_id):
            sign = effect_to_sign(edge["effect"])

            if sign == 0:
                excluded_edges.append({
                    "interaction_id": edge["interaction_id"],
                    "entity_a": node_name,
                    "entity_b": edge["target_name"],
                    "effect": edge["effect"],
                    "reason": "sign=0",
                })
                continue

            propagated = node_score * sign * edge["confidence_score"]
            target_id: str = edge["target_id"]
            target_name: str = edge["target_name"]

            contributing = {
                "interaction_id": edge["interaction_id"],
                "from_entity": node_name,
                "effect": edge["effect"],
                "sign": sign,
                "confidence_score": edge["confidence_score"],
            }

            # Skip back-edges to the seed — it is the perturbation source, not a target.
            if target_id == seed_entity_id:
                continue

            # Always record the path score (needed for correct multi-path combination).
            paths_to_entity[target_id].append((propagated, node_depth + 1, contributing))
            entity_names[target_id] = target_name

            # Only enqueue for further expansion if not yet visited (cycle prevention).
            if target_id not in visited:
                visited.add(target_id)
                frontier.append((target_id, target_name, propagated, node_depth + 1))

    # Combine multi-path scores and build affected list.
    affected: list[dict] = []
    for entity_id, path_list in paths_to_entity.items():
        pos_scores = [s for (s, _, _) in path_list if s > 0]
        neg_scores = [abs(s) for (s, _, _) in path_list if s < 0]

        pos_combined = combine_scores(pos_scores, exponent)
        neg_combined = combine_scores(neg_scores, exponent)
        final_score = pos_combined - neg_combined

        min_hop = min(h for (_, h, _) in path_list)

        affected.append({
            "entity_id": entity_id,
            "name": entity_names[entity_id],
            "perturbation_score": round(final_score, 6),
            "hop_depth": min_hop,
            "contributing_interactions": [ci for (_, _, ci) in path_list],
        })

    affected.sort(key=lambda a: abs(a["perturbation_score"]), reverse=True)

    return {
        "seed": {"entity_id": seed_entity_id, "name": seed_name, "perturbation_score": seed_score},
        "affected": affected,
        "excluded_edges": excluded_edges,
        "stats": {
            "n_affected": len(affected),
            "n_excluded_edges": len(excluded_edges),
            "duration_ms": 0,  # filled by caller
        },
    }
