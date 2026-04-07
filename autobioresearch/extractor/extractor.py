"""
Paper extractor: orchestrates chunking → LLM → snippet verification → DB persistence.
Produces Interaction upserts + EvidenceRecord inserts for each paper.
"""
from __future__ import annotations

import logging
from typing import Optional

from autobioresearch.config import AppConfig
from autobioresearch.extractor.claude_client import LLMClient, LLMTruncatedError, LLMTimeoutError
from autobioresearch.extractor.evidence_normalizer import EvidenceNormalizer
from autobioresearch.extractor.extraction_prompts import (
    EXTRACTION_FUNCTION,
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_TOOL,
)
from autobioresearch.extractor.normalizer import EntityNormalizer
from autobioresearch.extractor.verifier import InteractionVerifier
from autobioresearch.conflict.adjudicator import EvidenceAdjudicator
from autobioresearch.models import (
    EvidenceRecord,
    EvidenceType,
    ExtractionResult,
    ExtractedEntityRaw,
    ExtractedInteractionRaw,
    Interaction,
    InteractionContext,
    InteractionType,
    LiteralClaimRecord,
)
from autobioresearch.storage.repositories import Repositories
from autobioresearch.utils.text_utils import (
    chunk_text,
    fuzzy_snippet_check,
)

logger = logging.getLogger(__name__)
_truncation_review_logger = logging.getLogger("autobioresearch.truncation_review")


_REVIEW_ACTIONS: dict[str, str] = {
    "truncation": "verify paper is extractable or raise llm_max_tokens",
    "timeout": "verify API connectivity or raise llm_timeout_seconds",
}


def _log_truncation_review(
    paper_id: str,
    title: str,
    chunk_start: int,
    chunk_end: int,
    max_tokens: int,
    attempts: int,
    *,
    reason: str = "truncation",
) -> None:
    """Write a structured entry to the truncation review log for human triage."""
    if paper_id.startswith("pmid:"):
        paper_url = f"https://pubmed.ncbi.nlm.nih.gov/{paper_id.removeprefix('pmid:')}/"
    elif paper_id.startswith("s2:"):
        paper_url = f"https://www.semanticscholar.org/paper/{paper_id.removeprefix('s2:')}"
    else:
        paper_url = "n/a"
    title_short = (title or "")[:120] + ("…" if len(title or "") > 120 else "")
    action = _REVIEW_ACTIONS.get(reason, _REVIEW_ACTIONS["truncation"])
    _truncation_review_logger.warning(
        "\n"
        f"  paper_id    : {paper_id}\n"
        f"  url         : {paper_url}\n"
        f"  title       : {title_short}\n"
        f"  chunk       : {chunk_start}–{chunk_end} chars\n"
        f"  max_tokens  : {max_tokens}\n"
        f"  attempts    : {attempts}\n"
        f"  reason      : {reason}\n"
        f"  action      : flagged for human review — {action}"
    )


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
        self._verifier = InteractionVerifier(config=config, llm=llm)
        self._evidence_normalizer = EvidenceNormalizer()
        self._adjudicator = EvidenceAdjudicator()

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
            raw = None
            max_attempts = self._config.llm_truncation_retries + 1
            for attempt in range(max_attempts):
                try:
                    raw = self._llm.call_with_tool(
                        system=EXTRACTION_SYSTEM_PROMPT,
                        user=user_prompt,
                        tool=EXTRACTION_TOOL,
                        tool_function=EXTRACTION_FUNCTION,
                    )
                    break
                except (LLMTruncatedError, LLMTimeoutError) as exc:
                    reason = "timeout" if isinstance(exc, LLMTimeoutError) else "truncation"
                    label = reason.capitalize()
                    if attempt < max_attempts - 1:
                        logger.warning(
                            f"{label} on attempt {attempt + 1}/{max_attempts} for "
                            f"{paper_id} chunk @{chunk_start} — retrying"
                        )
                    else:
                        logger.warning(
                            f"{label} on all {max_attempts} attempt(s) for "
                            f"{paper_id} chunk @{chunk_start} — flagged for review"
                        )
                        _log_truncation_review(
                            paper_id, title, chunk_start, chunk_end,
                            self._config.llm_max_tokens, max_attempts,
                            reason=reason,
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

        # Deduplicate only exact repeated claims across chunks; preserve direction
        # and snippet-distinct findings from the same paper.
        all_interactions = self._deduplicate_interactions(all_interactions)
        all_interactions = [
            self._evidence_normalizer.normalize(
                self._verifier.verify_interaction(paper_id, title, text, interaction)
            )
            for interaction in all_interactions
        ]

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

        # Normalize all entities first, collect name->id mapping.
        # Paper-count telemetry is tracked per resolved entity_id, not per raw mention.
        name_to_id: dict[str, str] = {}
        counted_entity_ids: set[str] = set()
        created_entity_ids: set[str] = set()

        def resolve_entity(raw_entity: ExtractedEntityRaw) -> str:
            nonlocal new_entities
            entity_id, created_new = self._normalizer.normalize_with_metadata(raw_entity)
            name_to_id.setdefault(raw_entity.name, entity_id)
            if created_new and entity_id not in created_entity_ids:
                created_entity_ids.add(entity_id)
                new_entities += 1
            if entity_id not in counted_entity_ids:
                repos.entities.increment_paper_count(entity_id)
                counted_entity_ids.add(entity_id)
            return entity_id

        for ent in result.entities:
            if ent.name in name_to_id:
                continue
            resolve_entity(ent)

        # Persist interactions + evidence
        for raw_int in result.interactions:
            entity_a_id = name_to_id.get(raw_int.entity_a)
            entity_b_id = name_to_id.get(raw_int.entity_b)

            # If entity wasn't in the entities list, normalize on the fly
            if not entity_a_id:
                from autobioresearch.models import EntityType
                entity_a_id = resolve_entity(ExtractedEntityRaw(
                    name=raw_int.entity_a, entity_type=EntityType.UNKNOWN
                ))

            if not entity_b_id:
                from autobioresearch.models import EntityType
                entity_b_id = resolve_entity(ExtractedEntityRaw(
                    name=raw_int.entity_b, entity_type=EntityType.UNKNOWN
                ))

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

            claim_id = repos.literal_claims.insert(
                LiteralClaimRecord(
                    paper_id=result.paper_id,
                    entity_a_text=raw_int.entity_a,
                    entity_b_text=raw_int.entity_b,
                    interaction_type_text=str(raw_int.interaction_type),
                    direction_text=raw_int.direction,
                    effect_text=raw_int.effect,
                    evidence_type_text=str(raw_int.evidence_type),
                    organism_text=raw_int.organism,
                    tissue_cell_type_text=raw_int.tissue_cell_type,
                    condition_text=raw_int.condition,
                    assay_type_text=raw_int.assay_type,
                    evidence_subtype_text=raw_int.evidence_subtype,
                    confidence_text=raw_int.confidence,
                    confidence_score=raw_int.confidence_score,
                    snippet=raw_int.snippet,
                    reasoning=raw_int.reasoning,
                    verification_status=raw_int.verification_status,
                    verification_score=raw_int.verification_score,
                    verification_notes=raw_int.verification_notes,
                )
            )

            # Insert evidence record
            ev = EvidenceRecord(
                claim_id=claim_id,
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
                    normalized_organism=raw_int.normalized_organism,
                    normalized_tissue_cell_type=raw_int.normalized_tissue_cell_type,
                    normalized_condition=raw_int.normalized_condition,
                    normalized_assay_type=raw_int.normalized_assay_type,
                ),
                snippet=raw_int.snippet,
                verification_status=raw_int.verification_status,
                verification_score=raw_int.verification_score,
                verification_notes=raw_int.verification_notes,
            )
            adjudication_score, adjudication_notes = self._adjudicator.adjudicate(
                {
                    "evidence_type": ev.evidence_type.value,
                    "verification_score": ev.verification_score,
                    "normalized_organism": ev.context.normalized_organism,
                    "normalized_tissue_cell_type": ev.context.normalized_tissue_cell_type,
                    "normalized_condition": ev.context.normalized_condition,
                    "snippet": ev.snippet,
                },
                paper_row=None,
            )
            ev.adjudication_score = adjudication_score
            ev.adjudication_notes = adjudication_notes
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

    # LLM-invented interaction_type strings → valid InteractionType values.
    # Two categories of mistake seen in the wild:
    #   1. More specific sub-type used instead of the category  (binding → direct_binding)
    #   2. Effect word put in the wrong field                   (inhibits → unknown)
    # Extend this table as new variants appear in the logs.
    _INTERACTION_TYPE_ALIASES: dict[str, str] = {
        # sub-type → canonical category
        "binding": "direct_binding",
        "protein_binding": "direct_binding",
        "physical_interaction": "direct_binding",
        "physical_association": "proximal_association",
        "association": "proximal_association",
        "phosphorylation": "post_translational",
        "phosphorylates": "post_translational",
        "ubiquitination": "post_translational",
        "ubiquitinates": "post_translational",
        "acetylation": "post_translational",
        "methylation": "post_translational",
        "sumoylation": "post_translational",
        "cleavage": "post_translational",
        "proteolysis": "post_translational",
        "transcription_regulation": "transcriptional",
        "expression_regulation": "transcriptional",
        "translation_regulation": "translational",
        # effect words mistakenly placed in interaction_type field
        "inhibits": "unknown",
        "inhibition": "unknown",
        "activates": "unknown",
        "activation": "unknown",
        "induces": "unknown",
        "promotes": "unknown",
        "suppresses": "unknown",
        "upregulates": "unknown",
        "downregulates": "unknown",
        "regulates": "unknown",
        "regulation": "unknown",
        "modulates": "unknown",
    }

    # LLM-invented entity_type strings → valid EntityType values.
    # Extend this table as new variants appear in the logs.
    _ENTITY_TYPE_ALIASES: dict[str, str] = {
        "tissue_cell_type": "cell_type",
        "cell": "cell_type",
        "peptide": "molecule",         # short peptides behave like small molecules
        "treatment": "unknown",
        "drug": "molecule",
        "small_molecule": "molecule",
        "chemical": "molecule",
        "compound": "molecule",
        "protein_complex": "complex",
        "mrna": "rna",
        "mirna": "rna",
        "lncrna": "rna",
        "ncrna": "rna",
        # Modifications
        "post_translational_modification": "modification",
        "ptm": "modification",
        "phosphorylation": "modification",
        "acetylation": "modification",
        "methylation": "modification",
        "ubiquitination": "modification",
        "sumoylation": "modification",
        "glycosylation": "modification",
        # Structures / organelles / anatomical
        "organelle": "structure",
        "subcellular_structure": "structure",
        "anatomical_structure": "structure",
        "tissue": "structure",         # was "cell_type" — structure is more accurate
        "organ": "structure",          # was "cell_type" — structure is more accurate
        # Pathogens
        "virus": "pathogen",
        "bacteria": "pathogen",
        "bacterium": "pathogen",
        "fungus": "pathogen",
        "parasite": "pathogen",
        "microorganism": "pathogen",   # ambiguous — default to pathogen
        # Symbionts / commensals
        "commensal": "symbiont",
        "microbiome": "symbiont",
        "probiotic": "symbiont",
        # Small molecules / exogenous compounds
        "vitamin": "molecule",
        "supplement": "molecule",
        "steroid": "molecule",
        "lipid": "molecule",
        "fatty_acid": "molecule",
        "ion": "molecule",
        "nucleotide": "molecule",
        "amino_acid": "molecule",
        "sugar": "molecule",
        "glycan": "molecule",
        "carbohydrate": "molecule",
        "cofactor": "molecule",
        "hormone_small_molecule": "molecule",
        # Protein subtypes that LLMs name explicitly
        "enzyme": "protein",
        "kinase": "protein",
        "phosphatase": "protein",
        "protease": "protein",
        "receptor": "protein",
        "ion_channel": "protein",
        "transporter": "protein",
        "cytokine": "protein",
        "chemokine": "protein",
        "growth_factor": "protein",
        "transcription_factor": "protein",
        "antibody": "protein",
        "hormone_protein": "protein",
        "hormone": "protein",          # default hormones to protein; steroid hormones hit "steroid" first
        "neuropeptide": "protein",
        "adaptor_protein": "protein",
        "scaffold_protein": "protein",
        "chaperone": "protein",
        "ligand": "protein",           # most biological ligands are proteins; small-molecule ligands should be "molecule"
    }

    @classmethod
    def _norm(cls, value: str | None, fallback: str = "unknown") -> str:
        """
        Normalise LLM enum output to match our StrEnum values.
        Handles uppercase, spaces, and hyphens that local models sometimes emit,
        then applies an alias table for invented type names.
        e.g. "Direct Binding" → "direct_binding", "tissue_cell_type" → "cell_type"
        """
        if not isinstance(value, str) or not value:
            return fallback
        normalised = value.lower().strip().replace(" ", "_").replace("-", "_")
        return cls._ENTITY_TYPE_ALIASES.get(normalised, normalised)

    @classmethod
    def _norm_interaction_type(cls, value: str | None) -> str:
        """Like _norm but applies the interaction_type alias table."""
        if not isinstance(value, str) or not value:
            return "unknown"
        normalised = value.lower().strip().replace(" ", "_").replace("-", "_")
        return cls._INTERACTION_TYPE_ALIASES.get(normalised, normalised)

    @staticmethod
    def _norm_confidence(value: str | None) -> str:
        if not isinstance(value, str) or not value:
            value = "low"
        v = value.lower().strip()
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

                try:
                    confidence_score = max(0.0, min(1.0, float(int_data.get("confidence_score", 0.3))))
                except (ValueError, TypeError):
                    logger.debug(
                        "Non-numeric confidence_score %r for paper %s — defaulting to 0.3",
                        int_data.get("confidence_score"), paper_id,
                    )
                    confidence_score = 0.3

                interactions.append(ExtractedInteractionRaw(
                    entity_a=int_data["entity_a"],
                    entity_b=int_data["entity_b"],
                    interaction_type=self._norm_interaction_type(int_data.get("interaction_type")),
                    direction=self._norm(int_data.get("direction"), "undirected"),
                    effect=int_data.get("effect") or None,
                    evidence_type=self._norm(int_data.get("evidence_type"), "unknown"),
                    evidence_subtype=int_data.get("evidence_subtype"),
                    organism=int_data.get("organism"),
                    tissue_cell_type=int_data.get("tissue_cell_type"),
                    condition=int_data.get("condition"),
                    assay_type=int_data.get("assay_type"),
                    confidence=self._norm_confidence(int_data.get("confidence")),
                    confidence_score=confidence_score,
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
        Key: (entity_a_lower, entity_b_lower, interaction_type, effect, direction, snippet).
        Keeps the one with highest confidence_score.
        """
        seen: dict[tuple, ExtractedInteractionRaw] = {}
        for intr in interactions:
            key = (
                intr.entity_a.lower().strip(),
                intr.entity_b.lower().strip(),
                intr.interaction_type,
                (intr.effect or "").lower(),
                intr.direction,
                intr.snippet.strip(),
            )
            if key not in seen or intr.confidence_score > seen[key].confidence_score:
                seen[key] = intr
        return list(seen.values())
