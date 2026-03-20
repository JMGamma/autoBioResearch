"""
Paper extractor: orchestrates chunking → LLM → snippet verification → DB persistence.
Produces Interaction upserts + EvidenceRecord inserts for each paper.
"""
from __future__ import annotations

import logging
from typing import Optional

from autobioresearch.config import AppConfig
from autobioresearch.extractor.claude_client import LLMClient
from autobioresearch.extractor.extraction_prompts import (
    EXTRACTION_FUNCTION,
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_TOOL,
)
from autobioresearch.extractor.normalizer import EntityNormalizer
from autobioresearch.models import (
    EvidenceRecord,
    EvidenceType,
    ExtractionResult,
    ExtractedEntityRaw,
    ExtractedInteractionRaw,
    Interaction,
    InteractionContext,
    InteractionType,
)
from autobioresearch.storage.repositories import Repositories
from autobioresearch.utils.text_utils import (
    chunk_text,
    find_snippet_offsets,
    fuzzy_snippet_check,
)

logger = logging.getLogger(__name__)


class PaperExtractor:
    def __init__(self, config: AppConfig, llm: LLMClient, normalizer: EntityNormalizer):
        self._config = config
        self._llm = llm
        self._normalizer = normalizer

    def extract(self, paper_id: str, title: str, text: str) -> ExtractionResult:
        """
        Extract entities and interactions from paper text.
        text may be an abstract or (transiently) a full-text string.
        """
        chunks = chunk_text(
            text,
            max_chars=self._config.max_chunk_chars,
            overlap=self._config.chunk_overlap_chars,
        )

        all_entities: list[ExtractedEntityRaw] = []
        all_interactions: list[ExtractedInteractionRaw] = []
        all_notes: list[str] = []
        total_tokens: dict[str, int] = {}

        for chunk_start, chunk_end, chunk_text_str in chunks:
            user_prompt = self._build_user_prompt(paper_id, title, chunk_text_str)

            raw = self._llm.call_with_tool(
                system=EXTRACTION_SYSTEM_PROMPT,
                user=user_prompt,
                tool=EXTRACTION_TOOL,
                tool_function=EXTRACTION_FUNCTION,
            )

            if not raw:
                logger.warning(f"No LLM output for paper {paper_id}, chunk starting at {chunk_start}")
                continue

            entities, interactions, notes = self._parse_llm_output(
                raw, paper_id, chunk_text_str, chunk_start
            )
            all_entities.extend(entities)
            all_interactions.extend(interactions)
            if notes:
                all_notes.append(notes)

        # Deduplicate interactions by (entity_a, entity_b, type, effect, snippet_prefix)
        all_interactions = self._deduplicate_interactions(all_interactions)

        return ExtractionResult(
            paper_id=paper_id,
            entities=all_entities,
            interactions=all_interactions,
            extraction_notes=" | ".join(all_notes),
            token_usage=total_tokens,
        )

    def persist(self, result: ExtractionResult, repos: Repositories) -> tuple[int, int, int]:
        """
        Persist extraction result to DB.
        Returns (new_entities, new_interactions, new_evidence).
        """
        new_entities = 0
        new_interactions = 0
        new_evidence = 0

        # Normalize all entities first, collect name->id mapping
        name_to_id: dict[str, str] = {}
        seen_entity_names: set[str] = set()

        for ent in result.entities:
            if ent.name in seen_entity_names:
                continue
            seen_entity_names.add(ent.name)

            before_count = repos.entities.count()
            entity_id = self._normalizer.normalize(ent)
            after_count = repos.entities.count()
            if after_count > before_count:
                new_entities += 1

            name_to_id[ent.name] = entity_id
            repos.entities.increment_paper_count(entity_id)

        # Persist interactions + evidence
        for raw_int in result.interactions:
            entity_a_id = name_to_id.get(raw_int.entity_a)
            entity_b_id = name_to_id.get(raw_int.entity_b)

            # If entity wasn't in the entities list, normalize on the fly
            if not entity_a_id:
                from autobioresearch.models import EntityType
                entity_a_id = self._normalizer.normalize(ExtractedEntityRaw(
                    name=raw_int.entity_a, entity_type=EntityType.UNKNOWN
                ))
                name_to_id[raw_int.entity_a] = entity_a_id

            if not entity_b_id:
                from autobioresearch.models import EntityType
                entity_b_id = self._normalizer.normalize(ExtractedEntityRaw(
                    name=raw_int.entity_b, entity_type=EntityType.UNKNOWN
                ))
                name_to_id[raw_int.entity_b] = entity_b_id

            # Upsert interaction
            interaction = Interaction(
                entity_a_id=entity_a_id,
                entity_b_id=entity_b_id,
                interaction_type=raw_int.interaction_type,
                direction=raw_int.direction,
                effect=raw_int.effect,
            )
            interaction_id, is_new = repos.interactions.upsert(interaction)
            if is_new:
                new_interactions += 1

            # Insert evidence record
            ev = EvidenceRecord(
                interaction_id=interaction_id,
                paper_id=result.paper_id,
                evidence_type=raw_int.evidence_type,
                confidence=raw_int.confidence,
                confidence_score=raw_int.confidence_score,
                context=InteractionContext(
                    organism=raw_int.organism,
                    tissue_cell_type=raw_int.tissue_cell_type,
                    condition=raw_int.condition,
                    assay_type=raw_int.assay_type,
                    evidence_subtype=raw_int.evidence_subtype,
                ),
                snippet=raw_int.snippet,
            )
            is_new_ev = repos.evidence.insert(ev)
            if is_new_ev:
                new_evidence += 1
                repos.interactions.update_composite_confidence(interaction_id)

        return new_entities, new_interactions, new_evidence

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_user_prompt(self, paper_id: str, title: str, chunk: str) -> str:
        return (
            f"Extract all biological entities and their interactions from this paper text.\n\n"
            f"PAPER ID: {paper_id}\n"
            f"PAPER TITLE: {title or '(unknown)'}\n"
            f"TEXT:\n---\n{chunk}\n---\n\n"
            f"Use the extract_biology tool to return your findings. "
            f"For each interaction, include the EXACT verbatim snippet (max 400 chars) from the text above."
        )

    def _parse_llm_output(
        self,
        raw: dict,
        paper_id: str,
        source_text: str,
        chunk_offset: int,
    ) -> tuple[list[ExtractedEntityRaw], list[ExtractedInteractionRaw], str]:
        entities: list[ExtractedEntityRaw] = []
        interactions: list[ExtractedInteractionRaw] = []
        notes = raw.get("extraction_notes", "")

        for ent_data in raw.get("entities", []):
            try:
                entities.append(ExtractedEntityRaw(
                    name=ent_data["name"],
                    entity_type=ent_data.get("entity_type", "unknown"),
                    synonyms=ent_data.get("synonyms", []),
                    organism=ent_data.get("organism"),
                ))
            except Exception as e:
                logger.debug(f"Skipping malformed entity from paper {paper_id}: {e}")

        for int_data in raw.get("interactions", []):
            try:
                snippet = int_data.get("snippet", "")

                # Verify snippet is plausibly in the source text
                if snippet and len(snippet) >= self._config.min_snippet_length:
                    if not fuzzy_snippet_check(snippet, source_text, self._config.snippet_fuzzy_threshold):
                        logger.debug(
                            f"Snippet not found in source for paper {paper_id}; "
                            f"downgrading confidence. Snippet: {snippet[:80]}..."
                        )
                        int_data["confidence"] = "low"
                        int_data["confidence_score"] = min(
                            float(int_data.get("confidence_score", 0.5)), 0.3
                        )

                interactions.append(ExtractedInteractionRaw(
                    entity_a=int_data["entity_a"],
                    entity_b=int_data["entity_b"],
                    interaction_type=int_data.get("interaction_type", "unknown"),
                    direction=int_data.get("direction", "undirected"),
                    effect=int_data.get("effect"),
                    evidence_type=int_data.get("evidence_type", "unknown"),
                    evidence_subtype=int_data.get("evidence_subtype"),
                    organism=int_data.get("organism"),
                    tissue_cell_type=int_data.get("tissue_cell_type"),
                    condition=int_data.get("condition"),
                    assay_type=int_data.get("assay_type"),
                    confidence=int_data.get("confidence", "low"),
                    confidence_score=float(int_data.get("confidence_score", 0.3)),
                    snippet=snippet[:self._config.max_snippet_length],
                    reasoning=int_data.get("reasoning", ""),
                ))
            except Exception as e:
                logger.debug(f"Skipping malformed interaction from paper {paper_id}: {e}")

        return entities, interactions, notes

    def _deduplicate_interactions(
        self, interactions: list[ExtractedInteractionRaw]
    ) -> list[ExtractedInteractionRaw]:
        """
        Remove interactions extracted multiple times across chunks.
        Key: (entity_a_lower, entity_b_lower, interaction_type, effect).
        Keeps the one with highest confidence_score.
        """
        seen: dict[tuple, ExtractedInteractionRaw] = {}
        for intr in interactions:
            key = (
                intr.entity_a.lower().strip(),
                intr.entity_b.lower().strip(),
                intr.interaction_type,
                (intr.effect or "").lower(),
            )
            if key not in seen or intr.confidence_score > seen[key].confidence_score:
                seen[key] = intr
        return list(seen.values())
