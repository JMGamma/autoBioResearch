"""
Evidence-level adjudication helpers for controversial interactions.
"""
from __future__ import annotations

from collections.abc import Iterable


class EvidenceAdjudicator:
    _TYPE_QUALITY = {
        "structural": 1.0,
        "in_vivo": 0.9,
        "clinical": 0.85,
        "in_vitro": 0.75,
        "curated_db": 0.7,
        "genetic_screen": 0.65,
        "co_expression": 0.45,
        "computational": 0.35,
        "unknown": 0.3,
    }

    def adjudicate(self, evidence_row: dict, paper_row: dict | None = None) -> tuple[float, str]:
        quality = self._TYPE_QUALITY.get(evidence_row.get("evidence_type") or "unknown", 0.3)
        verification = float(evidence_row.get("verification_score") or 0.4)
        directness = 0.85 if evidence_row.get("snippet") else 0.3
        context_specificity = 0.1
        if evidence_row.get("normalized_organism"):
            context_specificity += 0.1
        if evidence_row.get("normalized_tissue_cell_type"):
            context_specificity += 0.1
        if evidence_row.get("normalized_condition"):
            context_specificity += 0.1
        recency = self._recency_score(paper_row.get("year") if paper_row else None)

        score = round(
            (quality * 0.35)
            + (verification * 0.3)
            + (directness * 0.15)
            + (context_specificity * 0.1)
            + (recency * 0.1),
            4,
        )
        note = (
            f"quality={quality:.2f}; verification={verification:.2f}; "
            f"directness={directness:.2f}; context={context_specificity:.2f}; recency={recency:.2f}"
        )
        return score, note

    def summarize_evidence_set(self, evidence_rows: Iterable[dict]) -> dict:
        evidence = list(evidence_rows)
        if not evidence:
            return {
                "mean_score": 0.0,
                "max_score": 0.0,
                "mean_verification": 0.0,
                "contexts": set(),
                "best_paper_id": None,
            }

        mean_score = sum(float(ev.get("adjudication_score") or 0.0) for ev in evidence) / len(evidence)
        max_score = max(float(ev.get("adjudication_score") or 0.0) for ev in evidence)
        mean_verification = sum(float(ev.get("verification_score") or 0.0) for ev in evidence) / len(evidence)
        best = max(evidence, key=lambda ev: float(ev.get("adjudication_score") or 0.0))
        contexts = {
            "|".join(filter(None, [
                ev.get("normalized_organism") or ev.get("organism"),
                ev.get("normalized_tissue_cell_type") or ev.get("tissue_cell_type"),
                ev.get("normalized_condition") or ev.get("condition"),
            ]))
            for ev in evidence
            if any([
                ev.get("normalized_organism") or ev.get("organism"),
                ev.get("normalized_tissue_cell_type") or ev.get("tissue_cell_type"),
                ev.get("normalized_condition") or ev.get("condition"),
            ])
        }
        return {
            "mean_score": round(mean_score, 4),
            "max_score": round(max_score, 4),
            "mean_verification": round(mean_verification, 4),
            "contexts": contexts,
            "best_paper_id": best.get("paper_id"),
        }

    def recommend_conflict_outcome(
        self,
        evidence_a: Iterable[dict],
        evidence_b: Iterable[dict],
        *,
        current_type: str,
    ) -> dict | None:
        stats_a = self.summarize_evidence_set(evidence_a)
        stats_b = self.summarize_evidence_set(evidence_b)
        contexts_a = stats_a["contexts"]
        contexts_b = stats_b["contexts"]
        same_context = bool(contexts_a & contexts_b) or (not contexts_a and not contexts_b)
        distinct_contexts = bool(contexts_a and contexts_b and not (contexts_a & contexts_b))
        score_gap = abs(stats_a["mean_score"] - stats_b["mean_score"])

        if distinct_contexts and min(stats_a["mean_score"], stats_b["mean_score"]) >= 0.45:
            return {
                "status": "resolved",
                "conflict_type": "context_dependent",
                "penalty_weight": 0.2,
                "resolution_paper_id": None,
                "note": (
                    "Adjudication indicates both claims are supported but in distinct normalized "
                    "contexts, favoring a context-dependent interpretation."
                ),
            }

        if same_context and score_gap >= 0.3:
            favored = stats_a if stats_a["mean_score"] >= stats_b["mean_score"] else stats_b
            return {
                "status": "resolved",
                "conflict_type": "true_conflict",
                "penalty_weight": 1.0,
                "resolution_paper_id": favored["best_paper_id"],
                "note": (
                    "Adjudication found a strong evidence-quality gap under comparable contexts, "
                    "favoring one claim as the current best-supported interpretation."
                ),
            }

        if current_type == "context_dependent" and distinct_contexts:
            return {
                "status": "resolved",
                "conflict_type": "context_dependent",
                "penalty_weight": 0.2,
                "resolution_paper_id": None,
                "note": "Normalized context differences support resolving this conflict as context-dependent.",
            }

        return None

    def _recency_score(self, year: int | None) -> float:
        if not year:
            return 0.4
        if year >= 2022:
            return 1.0
        if year >= 2018:
            return 0.8
        if year >= 2010:
            return 0.6
        return 0.4
