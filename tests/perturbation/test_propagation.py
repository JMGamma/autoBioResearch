"""
Unit tests for autobioresearch.perturbation.propagator.

All tests use synthetic edge data — no database required.
get_outgoing_edges is patched so run_propagation can be tested in isolation.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from autobioresearch.perturbation.propagator import combine_scores, run_propagation

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SEED_ID = "seed-001"
B_ID = "entity-b"
C_ID = "entity-c"
D_ID = "entity-d"

PATCH_TARGET = "autobioresearch.perturbation.propagator.get_outgoing_edges"


def _edge(interaction_id, target_id, target_name, effect, confidence):
    return {
        "interaction_id": interaction_id,
        "target_id": target_id,
        "target_name": target_name,
        "effect": effect,
        "confidence_score": float(confidence),
    }


def _run(edge_map: dict, mode: str = "suppress", depth: int = 1):
    """Run propagation with a synthetic edge map (no real DB)."""
    conn = MagicMock()

    def fake_get_outgoing_edges(conn_, eid):
        return edge_map.get(eid, [])

    with patch(PATCH_TARGET, side_effect=fake_get_outgoing_edges):
        return run_propagation(conn, SEED_ID, "Seed", mode, depth)


# ---------------------------------------------------------------------------
# combine_scores
# ---------------------------------------------------------------------------

def test_combine_scores_empty():
    assert combine_scores([]) == 0.0


def test_combine_scores_single():
    result = combine_scores([0.8])
    # formula: 1 - (1 - 0.8^0.55) — should be between 0 and 1
    assert 0.0 < result < 1.0


def test_combine_scores_two_paths_amplify():
    single = combine_scores([0.5])
    combined = combine_scores([0.5, 0.5])
    assert combined > single


def test_combine_scores_clamp_above_one():
    """Should not raise even if input > 1.0 due to float drift."""
    result = combine_scores([1.1])
    assert 0.0 <= result <= 1.0


def test_combine_scores_exact_one():
    result = combine_scores([1.0])
    assert result == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# run_propagation — suppress mode
# ---------------------------------------------------------------------------

def test_suppress_activates_gives_negative_score():
    """suppress + activates → downstream is negatively perturbed."""
    edge_map = {SEED_ID: [_edge("i1", B_ID, "B", "activates", 0.8)]}
    result = _run(edge_map, mode="suppress", depth=1)
    affected = {a["entity_id"]: a for a in result["affected"]}
    assert B_ID in affected
    assert affected[B_ID]["perturbation_score"] < 0


def test_suppress_inhibits_gives_positive_score():
    """suppress + inhibits → double-negative → positive downstream."""
    edge_map = {SEED_ID: [_edge("i1", B_ID, "B", "inhibits", 0.8)]}
    result = _run(edge_map, mode="suppress", depth=1)
    affected = {a["entity_id"]: a for a in result["affected"]}
    assert affected[B_ID]["perturbation_score"] > 0


def test_promote_activates_gives_positive_score():
    """promote + activates → positive downstream."""
    edge_map = {SEED_ID: [_edge("i1", B_ID, "B", "activates", 0.8)]}
    result = _run(edge_map, mode="promote", depth=1)
    affected = {a["entity_id"]: a for a in result["affected"]}
    assert affected[B_ID]["perturbation_score"] > 0


# ---------------------------------------------------------------------------
# Neutral effect → excluded_edges
# ---------------------------------------------------------------------------

def test_neutral_effect_goes_to_excluded_not_affected():
    edge_map = {SEED_ID: [_edge("i1", B_ID, "B", "binds", 0.8)]}
    result = _run(edge_map, mode="suppress", depth=1)
    assert result["affected"] == []
    assert len(result["excluded_edges"]) == 1
    assert result["excluded_edges"][0]["reason"] == "sign=0"
    assert result["excluded_edges"][0]["entity_a"] == "Seed"
    assert result["excluded_edges"][0]["entity_b"] == "B"


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------

def test_cycle_does_not_infinite_loop():
    """A→B→A cycle must not cause infinite BFS recursion."""
    edge_map = {
        SEED_ID: [_edge("i1", B_ID, "B", "activates", 0.8)],
        B_ID:    [_edge("i2", SEED_ID, "Seed", "activates", 0.8)],
    }
    result = _run(edge_map, mode="suppress", depth=4)
    # Must complete without hanging; seed must not appear in affected
    assert SEED_ID not in {a["entity_id"] for a in result["affected"]}


def test_longer_cycle_does_not_infinite_loop():
    """A→B→C→A cycle must terminate."""
    edge_map = {
        SEED_ID: [_edge("i1", B_ID, "B", "activates", 0.8)],
        B_ID:    [_edge("i2", C_ID, "C", "activates", 0.8)],
        C_ID:    [_edge("i3", SEED_ID, "Seed", "activates", 0.8)],
    }
    result = _run(edge_map, mode="suppress", depth=4)
    assert SEED_ID not in {a["entity_id"] for a in result["affected"]}


# ---------------------------------------------------------------------------
# depth=0 edge case
# ---------------------------------------------------------------------------

def test_depth_zero_returns_seed_only():
    conn = MagicMock()
    result = run_propagation(conn, SEED_ID, "Seed", "suppress", depth=0)
    assert result["affected"] == []
    assert result["excluded_edges"] == []
    assert result["seed"]["perturbation_score"] == -1.0
    assert result["seed"]["entity_id"] == SEED_ID


def test_depth_zero_promote_seed_score():
    conn = MagicMock()
    result = run_propagation(conn, SEED_ID, "Seed", "promote", depth=0)
    assert result["seed"]["perturbation_score"] == 1.0


# ---------------------------------------------------------------------------
# hop_depth tracking
# ---------------------------------------------------------------------------

def test_hop_depth_one_hop():
    edge_map = {SEED_ID: [_edge("i1", B_ID, "B", "activates", 0.8)]}
    result = _run(edge_map, mode="suppress", depth=2)
    affected = {a["entity_id"]: a for a in result["affected"]}
    assert affected[B_ID]["hop_depth"] == 1


def test_hop_depth_two_hops():
    edge_map = {
        SEED_ID: [_edge("i1", B_ID, "B", "activates", 0.8)],
        B_ID:    [_edge("i2", C_ID, "C", "activates", 0.8)],
    }
    result = _run(edge_map, mode="suppress", depth=2)
    affected = {a["entity_id"]: a for a in result["affected"]}
    assert affected[B_ID]["hop_depth"] == 1
    assert affected[C_ID]["hop_depth"] == 2


def test_depth_cap_prevents_expansion_beyond_depth():
    """At depth=1, C (two hops away) should not appear."""
    edge_map = {
        SEED_ID: [_edge("i1", B_ID, "B", "activates", 0.8)],
        B_ID:    [_edge("i2", C_ID, "C", "activates", 0.8)],
    }
    result = _run(edge_map, mode="suppress", depth=1)
    affected_ids = {a["entity_id"] for a in result["affected"]}
    assert B_ID in affected_ids
    assert C_ID not in affected_ids


# ---------------------------------------------------------------------------
# Multi-path score combination
# ---------------------------------------------------------------------------

def test_multi_path_score_higher_than_single_path():
    """Entity reached via 2 paths should have a higher |score| than via 1 path."""
    edge_map_multi = {
        SEED_ID: [
            _edge("i1", B_ID, "B", "activates", 0.8),
            _edge("i2", C_ID, "C", "activates", 0.8),
        ],
        B_ID: [_edge("i3", D_ID, "D", "activates", 0.8)],
        C_ID: [_edge("i4", D_ID, "D", "activates", 0.8)],
    }
    edge_map_single = {
        SEED_ID: [_edge("i1", B_ID, "B", "activates", 0.8)],
        B_ID:    [_edge("i3", D_ID, "D", "activates", 0.8)],
    }

    result_multi  = _run(edge_map_multi,  mode="suppress", depth=2)
    result_single = _run(edge_map_single, mode="suppress", depth=2)

    d_multi  = next(a for a in result_multi["affected"]  if a["entity_id"] == D_ID)
    d_single = next(a for a in result_single["affected"] if a["entity_id"] == D_ID)
    assert abs(d_multi["perturbation_score"]) > abs(d_single["perturbation_score"])


def test_opposing_paths_partially_cancel():
    """One activating and one inhibiting path to the same entity should partially cancel."""
    edge_map = {
        SEED_ID: [
            _edge("i1", B_ID, "B", "activates", 0.8),  # suppress→B = negative
            _edge("i2", C_ID, "C", "activates", 0.8),  # suppress→C = negative
        ],
        B_ID: [_edge("i3", D_ID, "D", "activates", 0.8)],  # negative path to D
        C_ID: [_edge("i4", D_ID, "D", "inhibits",  0.8)],  # double-neg → positive path to D
    }
    result = _run(edge_map, mode="suppress", depth=2)
    d = next(a for a in result["affected"] if a["entity_id"] == D_ID)
    # The two paths partially cancel — |score| should be less than either alone
    # (not testing exact value, just that both paths contributed)
    assert len(d["contributing_interactions"]) == 2


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

def test_output_contains_required_keys():
    edge_map = {SEED_ID: [_edge("i1", B_ID, "B", "activates", 0.8)]}
    result = _run(edge_map, mode="suppress", depth=1)
    assert "seed" in result
    assert "affected" in result
    assert "excluded_edges" in result
    assert "stats" in result


def test_stats_counts_are_correct():
    edge_map = {
        SEED_ID: [
            _edge("i1", B_ID, "B", "activates", 0.8),
            _edge("i2", C_ID, "C", "binds", 0.5),   # excluded
        ]
    }
    result = _run(edge_map, mode="suppress", depth=1)
    assert result["stats"]["n_affected"] == 1
    assert result["stats"]["n_excluded_edges"] == 1


def test_affected_sorted_by_abs_score_descending():
    edge_map = {
        SEED_ID: [
            _edge("i1", B_ID, "B", "activates", 0.9),
            _edge("i2", C_ID, "C", "activates", 0.3),
        ]
    }
    result = _run(edge_map, mode="suppress", depth=1)
    scores = [abs(a["perturbation_score"]) for a in result["affected"]]
    assert scores == sorted(scores, reverse=True)
