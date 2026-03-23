"""
Targeted verifier for high-impact interaction claims.

Only medium/high-confidence or conflict-linked claims are verified.
The verifier prefers an LLM judgment but falls back to conservative heuristics
so the loop remains usable when the verifier model is unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass

from autobioresearch.config import AppConfig
from autobioresearch.extractor.claude_client import LLMClient
from autobioresearch.extractor.extraction_prompts import (
    VERIFICATION_FUNCTION,
    VERIFICATION_SYSTEM_PROMPT,
    VERIFICATION_TOOL,
)
from autobioresearch.models import ExtractedInteractionRaw


@dataclass
class VerificationOutcome:
    status: str
    score: float
    notes: str


class InteractionVerifier:
    def __init__(self, config: AppConfig, llm: LLMClient | None):
        self._config = config
        self._llm = llm

    def should_verify(self, interaction: ExtractedInteractionRaw, *, conflict_linked: bool = False) -> bool:
        if conflict_linked:
            return True
        return interaction.confidence_score >= self._config.verification_min_confidence_score

    def verify_interaction(
        self,
        paper_id: str,
        title: str,
        source_text: str,
        interaction: ExtractedInteractionRaw,
        *,
        conflict_linked: bool = False,
    ) -> ExtractedInteractionRaw:
        if not self._config.verification_enabled or not self.should_verify(interaction, conflict_linked=conflict_linked):
            return interaction

        result = None
        if self._llm is not None:
            result = self._llm.call_with_tool(
                system=VERIFICATION_SYSTEM_PROMPT,
                user=self._build_prompt(paper_id, title, source_text, interaction),
                tool=VERIFICATION_TOOL,
                tool_function=VERIFICATION_FUNCTION,
            )

        if result:
            supported_fields = result.get("supported_fields", {})
            status = result.get("overall_status", "needs_review")
            score = float(result.get("verification_score", 0.0))
            notes = result.get("notes", "")
            if not all(bool(supported_fields.get(field, False)) for field in self._field_names(interaction)):
                status = "needs_review"
            return interaction.model_copy(update={
                "verification_status": status,
                "verification_score": score,
                "verification_notes": notes[:500],
            })

        outcome = self._heuristic_verify(interaction, source_text)
        return interaction.model_copy(update={
            "verification_status": outcome.status,
            "verification_score": outcome.score,
            "verification_notes": outcome.notes,
        })

    def _build_prompt(
        self,
        paper_id: str,
        title: str,
        source_text: str,
        interaction: ExtractedInteractionRaw,
    ) -> str:
        abstract_excerpt = source_text[:3000]
        return (
            f"Validate this extracted interaction claim.\n\n"
            f"PAPER ID: {paper_id}\n"
            f"TITLE: {title}\n"
            f"CLAIM:\n"
            f"- entity_a: {interaction.entity_a}\n"
            f"- entity_b: {interaction.entity_b}\n"
            f"- interaction_type: {interaction.interaction_type}\n"
            f"- direction: {interaction.direction}\n"
            f"- effect: {interaction.effect}\n"
            f"- organism: {interaction.organism}\n"
            f"- tissue_cell_type: {interaction.tissue_cell_type}\n"
            f"- condition: {interaction.condition}\n"
            f"- confidence: {interaction.confidence} ({interaction.confidence_score:.2f})\n\n"
            f"SUPPORTING SNIPPET:\n{interaction.snippet}\n\n"
            f"PAPER TEXT EXCERPT:\n{abstract_excerpt}\n"
        )

    def _heuristic_verify(self, interaction: ExtractedInteractionRaw, source_text: str) -> VerificationOutcome:
        haystack = f"{interaction.snippet}\n{source_text[:4000]}".lower()
        checks = []

        if interaction.effect:
            checks.append(((interaction.effect or "").lower() in haystack, f"effect={interaction.effect}"))
        if interaction.organism:
            checks.append((interaction.organism.lower() in haystack, f"organism={interaction.organism}"))
        if interaction.tissue_cell_type:
            checks.append((interaction.tissue_cell_type.lower() in haystack, f"tissue={interaction.tissue_cell_type}"))
        if interaction.condition:
            checks.append((interaction.condition.lower() in haystack, f"condition={interaction.condition}"))
        if interaction.direction not in ("undirected", "bidirectional", "", None):
            direction_supported = (
                interaction.entity_a.lower() in haystack and interaction.entity_b.lower() in haystack
            )
            checks.append((direction_supported, f"direction={interaction.direction}"))

        failed = [label for ok, label in checks if not ok]
        if failed:
            return VerificationOutcome(
                status="needs_review",
                score=max(0.2, 1.0 - (0.2 * len(failed))),
                notes="Heuristic verifier could not confirm: " + ", ".join(failed[:3]),
            )
        return VerificationOutcome(
            status="verified",
            score=0.8 if checks else 0.6,
            notes="Heuristic verifier found support for claimed fields.",
        )

    def _field_names(self, interaction: ExtractedInteractionRaw) -> list[str]:
        fields = ["effect", "direction"]
        if interaction.organism:
            fields.append("organism")
        if interaction.tissue_cell_type:
            fields.append("tissue_cell_type")
        if interaction.condition:
            fields.append("condition")
        return fields
