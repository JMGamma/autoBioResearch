"""Unit tests for autobioresearch.perturbation.sign_map. No DB required."""
import pytest

from autobioresearch.perturbation.sign_map import effect_to_sign, normalize_effect


def test_normalize_none():
    assert normalize_effect(None) == ""


def test_normalize_strips_and_lowercases():
    assert normalize_effect("  Activates  ") == "activates"


def test_normalize_empty_string():
    assert normalize_effect("") == ""


@pytest.mark.parametrize("effect", [
    "activates", "activation",
    "promotes", "promotion",
    "upregulates", "upregulation",
    "stimulates", "stimulation",
    "induces", "induction",
    "increases",
    "enhances", "enhancement",
])
def test_activating_effects(effect):
    assert effect_to_sign(effect) == 1, f"Expected +1 for '{effect}'"


@pytest.mark.parametrize("effect", [
    "inhibits", "inhibition",
    "suppresses", "suppression",
    "downregulates", "downregulation",
    "blocks", "blocking",
    "represses", "repression",
    "decreases",
    "reduces", "reduction",
])
def test_inhibitory_effects(effect):
    assert effect_to_sign(effect) == -1, f"Expected -1 for '{effect}'"


@pytest.mark.parametrize("effect", [
    "binds", "binding",
    "phosphorylates", "dephosphorylates",
    "regulates", "regulation",
    "modulates", "modulation",
    "recruits", "localizes", "transports",
    "ubiquitinates", "methylates", "acetylates",
    "catalyzes", "cleaves",
    "reacts_with",
    "interacts", "interaction",
    "affects",
    "unknown",
])
def test_neutral_effects(effect):
    assert effect_to_sign(effect) == 0, f"Expected 0 for '{effect}'"


def test_null_literal_string():
    """LLM sometimes emits the literal string 'null' — must map to 0."""
    assert effect_to_sign("null") == 0


def test_none_input():
    assert effect_to_sign(None) == 0


def test_empty_string():
    assert effect_to_sign("") == 0


def test_unknown_effect_defaults_to_zero():
    """Effects not in SIGN_MAP must silently become 0 (not raise)."""
    assert effect_to_sign("increases expression of") == 0   # multi-word phrase, not in map
    assert effect_to_sign("some_novel_llm_term") == 0
    assert effect_to_sign("synthetic_lethality") == 0


def test_newly_added_activating_aliases():
    for effect in ["stabilizes", "stabilises", "upregulated", "synergizes", "restores",
                   "ameliorates", "alleviates", "improves", "drives", "sensitizes"]:
        assert effect_to_sign(effect) == 1, f"Expected +1 for '{effect}'"


def test_newly_added_inhibitory_aliases():
    for effect in ["degrades", "destabilizes", "attenuates", "silences",
                   "down-regulates", "downregulated", "lowers", "impairs", "impedes"]:
        assert effect_to_sign(effect) == -1, f"Expected -1 for '{effect}'"


def test_newly_added_mechanistic_ptms():
    for effect in ["deubiquitinates", "deubiquitylates", "deacetylates",
                   "demethylates", "sumoylates", "glycosylates", "hydrolyzes"]:
        assert effect_to_sign(effect) == 0, f"Expected 0 for '{effect}'"


def test_case_insensitive_lookup():
    """SIGN_MAP lookup is case-insensitive via normalize_effect."""
    assert effect_to_sign("ACTIVATES") == 1
    assert effect_to_sign("Inhibits") == -1
    assert effect_to_sign("BINDS") == 0
