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

# Known non-mammalian organisms — interactions with these as sole organism are skipped.
# Lowercase; matched as substring so "arabidopsis thaliana" catches "arabidopsis".
_NON_MAMMALIAN_ORGANISMS: frozenset[str] = frozenset({
    # Plants
    "arabidopsis", "oryza sativa", "zea mays", "solanum", "nicotiana",
    "glycine max", "triticum", "hordeum", "populus", "physcomitrella",
    # Yeast / fungi
    "saccharomyces", "schizosaccharomyces", "candida", "aspergillus",
    "neurospora", "cryptococcus", "pichia",
    # Insects
    "drosophila", "aedes", "anopheles", "bombyx", "tribolium",
    # Nematodes
    "caenorhabditis", "c. elegans",
    # Fish
    "danio rerio", "zebrafish", "oryzias", "medaka", "tetraodon",
    "takifugu", "fundulus", "salmo", "oncorhynchus",
    # Amphibians
    "xenopus", "ambystoma",
    # Birds
    "gallus gallus", "chicken", "meleagris", "taeniopygia",
    # Bacteria
    "escherichia", "bacillus", "staphylococcus", "streptococcus",
    "pseudomonas", "mycobacterium", "salmonella", "helicobacter",
    "listeria", "campylobacter", "vibrio", "clostridium",
    # Archaea
    "methanococcus", "halobacterium", "sulfolobus",
})


def _is_non_mammalian(organism: Optional[str]) -> bool:
    """Return True if the organism string clearly identifies a non-mammalian species."""
    if not organism:
        return False  # unspecified — assume mammalian context
    org_lower = organism.lower().strip()
    return any(nm in org_lower for nm in _NON_MAMMALIAN_ORGANISMS)


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
        chunks = list(chunk_text(
            text,
            max_chars=self._config.max_chunk_chars,
            overlap=self._config.chunk_overlap_chars,
        ))

        all_entities: list[ExtractedEntityRaw] = []
        all_interactions: list[ExtractedInteractionRaw] = []
        all_notes: list[str] = []
        total_tokens: dict[str, int] = {}

        n_chunks = len(chunks)
        if n_chunks > 1:
            logger.debug(f"{paper_id}: {n_chunks} chunk(s), text_len={len(text)}")

        for chunk_idx, (chunk_start, chunk_end, chunk_text_str) in enumerate(chunks):
            user_prompt = self._build_user_prompt(paper_id, title, chunk_text_str)

            if n_chunks > 1:
                logger.debug(f"Calling LLM for {paper_id} chunk {chunk_idx+1}/{n_chunks} @{chunk_start}")
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

    # ------------------------------------------------------------------
    # Enum normalisation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _norm(value: str | None, fallback: str = "unknown") -> str:
        """
        Normalise LLM enum output to match our StrEnum values.
        Handles uppercase, spaces, and hyphens that local models sometimes emit.
        e.g. "Direct Binding" → "direct_binding", "IN-VITRO" → "in_vitro"
        """
        if not value:
            return fallback
        return value.lower().strip().replace(" ", "_").replace("-", "_")

    @staticmethod
    def _norm_confidence(value: str | None) -> str:
        v = (value or "low").lower().strip()
        return v if v in ("high", "medium", "low") else "low"

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

        entities_raw = raw.get("entities", [])
        interactions_raw = raw.get("interactions", [])

        # Guard: model sometimes returns a string or None instead of a list
        if not isinstance(entities_raw, list):
            logger.warning(
                f"Paper {paper_id}: 'entities' field is {type(entities_raw).__name__}, "
                f"expected list — skipping. Value: {str(entities_raw)[:120]}"
            )
            entities_raw = []
        if not isinstance(interactions_raw, list):
            logger.warning(
                f"Paper {paper_id}: 'interactions' field is {type(interactions_raw).__name__}, "
                f"expected list — skipping. Value: {str(interactions_raw)[:120]}"
            )
            interactions_raw = []

        for ent_data in entities_raw:
            if not isinstance(ent_data, dict):
                logger.warning(
                    f"Paper {paper_id}: entity entry is {type(ent_data).__name__}, "
                    f"expected dict — skipping. Value: {str(ent_data)[:80]}"
                )
                continue
            try:
                entities.append(ExtractedEntityRaw(
                    name=ent_data["name"],
                    entity_type=self._norm(ent_data.get("entity_type"), "unknown"),
                    synonyms=ent_data.get("synonyms", []),
                    organism=ent_data.get("organism"),
                ))
            except Exception as e:
                logger.warning(
                    f"Skipping malformed entity from paper {paper_id}: {e} | raw={ent_data}"
                )

        for int_data in interactions_raw:
            if not isinstance(int_data, dict):
                logger.warning(
                    f"Paper {paper_id}: interaction entry is {type(int_data).__name__}, "
                    f"expected dict — skipping. Value: {str(int_data)[:80]}"
                )
                continue
            try:
                # Require both entity names
                if not int_data.get("entity_a") or not int_data.get("entity_b"):
                    logger.warning(
                        f"Paper {paper_id}: interaction missing entity_a or entity_b — skipping. "
                        f"entity_a={int_data.get('entity_a')!r} entity_b={int_data.get('entity_b')!r}"
                    )
                    continue

                # Skip non-mammalian interactions
                if _is_non_mammalian(int_data.get("organism")):
                    logger.debug(
                        f"Skipping non-mammalian interaction "
                        f"({int_data.get('organism')}): "
                        f"{int_data.get('entity_a')} <-> {int_data.get('entity_b')}"
                    )
                    continue

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
                    interaction_type=self._norm(int_data.get("interaction_type"), "unknown"),
                    direction=self._norm(int_data.get("direction"), "undirected"),
                    effect=int_data.get("effect") or None,
                    evidence_type=self._norm(int_data.get("evidence_type"), "unknown"),
                    evidence_subtype=int_data.get("evidence_subtype"),
                    organism=int_data.get("organism"),
                    tissue_cell_type=int_data.get("tissue_cell_type"),
                    condition=int_data.get("condition"),
                    assay_type=int_data.get("assay_type"),
                    confidence=self._norm_confidence(int_data.get("confidence")),
                    confidence_score=float(int_data.get("confidence_score", 0.3)),
                    snippet=snippet[:self._config.max_snippet_length],
                    reasoning=int_data.get("reasoning") or "",
                ))
            except Exception as e:
                logger.warning(
                    f"Skipping malformed interaction from paper {paper_id}: {e} | "
                    f"entity_a={int_data.get('entity_a')} entity_b={int_data.get('entity_b')} "
                    f"interaction_type={int_data.get('interaction_type')} "
                    f"evidence_type={int_data.get('evidence_type')}"
                )

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
