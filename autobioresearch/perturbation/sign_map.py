"""Shared effect-to-sign mapping for perturbation propagation. Reused by Phase 1 API."""

from __future__ import annotations


def normalize_effect(effect: str | None) -> str:
    """Lowercase and strip whitespace for consistent SIGN_MAP lookup."""
    if effect is None:
        return ""
    return effect.strip().lower()


# Maps normalized effect strings to perturbation signs:
#   +1 = activating / upregulating
#   -1 = inhibitory / downregulating
#    0 = neutral / mechanistic (excluded from propagation, collected in excluded_edges)
SIGN_MAP: dict[str, int] = {
    # Activating (+1)
    "activates": 1,
    "activation": 1,
    "promotes": 1,
    "promotion": 1,
    "upregulates": 1,
    "upregulation": 1,
    "upregulated": 1,       # LLM past-tense variant
    "upregulated in": 1,    # LLM past-tense variant
    "stimulates": 1,
    "stimulation": 1,
    "induces": 1,
    "induction": 1,
    "increases": 1,
    "enhances": 1,
    "enhancement": 1,
    "stabilizes": 1,        # protein/complex stabilization is functionally activating
    "stabilises": 1,        # British spelling variant
    "drives": 1,
    "restores": 1,          # restores activity = re-activates
    "synergizes": 1,
    "synergizes with": 1,
    "synergy": 1,
    "sensitizes": 1,        # makes target more responsive / vulnerable
    "improves": 1,
    "ameliorates": 1,
    "alleviates": 1,
    # Inhibitory (-1)
    "inhibits": -1,
    "inhibition": -1,
    "suppresses": -1,
    "suppression": -1,
    "downregulates": -1,
    "downregulation": -1,
    "downregulated": -1,    # LLM past-tense variant
    "down-regulates": -1,   # hyphenated variant
    "blocks": -1,
    "blocking": -1,
    "represses": -1,
    "repression": -1,
    "decreases": -1,
    "reduces": -1,
    "reduction": -1,
    "degrades": -1,         # protein degradation is inhibitory
    "destabilizes": -1,
    "attenuates": -1,
    "silences": -1,         # gene silencing
    "lowers": -1,
    "impairs": -1,
    "impedes": -1,
    # Neutral / mechanistic (0 — excluded from propagation)
    "regulates": 0,
    "regulation": 0,
    "modulates": 0,
    "modulation": 0,
    "affects": 0,
    "binds": 0,
    "binding": 0,
    "interacts": 0,
    "interaction": 0,
    "reacts_with": 0,
    "phosphorylates": 0,
    "dephosphorylates": 0,
    "ubiquitinates": 0,
    "deubiquitinates": 0,   # reversal of ubiquitination — mechanistic
    "deubiquitylates": 0,   # alternate spelling
    "methylates": 0,
    "demethylates": 0,
    "acetylates": 0,
    "deacetylates": 0,
    "sumoylates": 0,
    "glycosylates": 0,
    "hydrolyzes": 0,
    "catalyzes": 0,
    "cleaves": 0,
    "recruits": 0,
    "localizes": 0,
    "transports": 0,
    "modifies": 0,
    "modification": 0,
    "unknown": 0,
    "null": 0,  # LLM artifact: literal string "null"
    "": 0,      # NULL effect field
}

def effect_to_sign(effect: str | None) -> int:
    """
    Return the perturbation sign for a given effect string.

    Returns +1 (activating), -1 (inhibitory), or 0 (neutral/unknown).
    Unknown effect strings default to 0 — they are not errors; they become excluded_edges.
    """
    return SIGN_MAP.get(normalize_effect(effect), 0)


def unmapped_effect_summary(conn) -> dict:
    """
    Query the interactions table for effect strings not covered by SIGN_MAP.

    Returns a summary dict for cycle-level logging so unmapped aliases can be
    spotted when they accumulate enough to warrant a new pass.

    Keys:
        total_unmapped_interactions  — row count with unmapped effects
        distinct_unmapped_effects    — number of distinct unknown strings
        top_unmapped                 — list of {"effect": str, "count": int}, up to 10
    """
    from sqlalchemy import text  # lazy import — sign_map has no other DB dependency

    rows = conn.execute(text("""
        SELECT LOWER(TRIM(COALESCE(effect, ''))) AS eff, COUNT(*) AS cnt
        FROM   interactions
        GROUP  BY LOWER(TRIM(COALESCE(effect, '')))
    """)).fetchall()

    unmapped = [(eff, cnt) for eff, cnt in rows if eff not in SIGN_MAP]
    total = sum(cnt for _, cnt in unmapped)
    top = sorted(unmapped, key=lambda x: x[1], reverse=True)[:10]

    return {
        "total_unmapped_interactions": total,
        "distinct_unmapped_effects": len(unmapped),
        "top_unmapped": [{"effect": e, "count": c} for e, c in top],
    }
