from enum import StrEnum
from typing import Optional
from pydantic import BaseModel, Field
import uuid


class InteractionType(StrEnum):
    DIRECT_BINDING = "direct_binding"
    ENZYMATIC = "enzymatic"
    SIGNALING = "signaling"
    GENETIC = "genetic"
    TRANSCRIPTIONAL = "transcriptional"
    TRANSLATIONAL = "translational"
    POST_TRANSLATIONAL = "post_translational"
    METABOLIC = "metabolic"
    PROXIMAL_ASSOCIATION = "proximal_association"
    CO_EXPRESSION = "co_expression"
    TRANSPORT = "transport"
    UNKNOWN = "unknown"


class EvidenceType(StrEnum):
    IN_VITRO = "in_vitro"
    IN_VIVO = "in_vivo"
    STRUCTURAL = "structural"
    COMPUTATIONAL = "computational"
    CO_EXPRESSION = "co_expression"
    GENETIC_SCREEN = "genetic_screen"
    CLINICAL = "clinical"
    UNKNOWN = "unknown"


class InteractionContext(BaseModel):
    """Biological context for a single piece of evidence."""
    organism: Optional[str] = None
    tissue_cell_type: Optional[str] = None
    condition: Optional[str] = None       # disease state, drug treatment, etc.
    temperature: Optional[str] = None
    concentration: Optional[str] = None
    assay_type: Optional[str] = None
    evidence_subtype: Optional[str] = None  # western_blot, co_ip, cryo_em, etc.


class EvidenceRecord(BaseModel):
    """
    One paper's evidence for an interaction.
    Stored in the `evidence` table; multiple records can reference the same interaction.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    interaction_id: str                   # set after interaction upsert
    paper_id: str
    evidence_type: EvidenceType
    confidence: str                       # high | medium | low
    confidence_score: float = Field(ge=0.0, le=1.0)
    context: InteractionContext = Field(default_factory=InteractionContext)
    snippet: str
    snippet_start: Optional[int] = None
    snippet_end: Optional[int] = None


class Interaction(BaseModel):
    """
    A unique interaction claim between two entities.
    Identified by (entity_a_id, entity_b_id, interaction_type, effect).
    Confidence is aggregated from all supporting evidence records.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    entity_a_id: str
    entity_b_id: str
    interaction_type: InteractionType
    direction: str = "undirected"         # A_to_B | B_to_A | bidirectional | undirected
    effect: Optional[str] = None          # activates | inhibits | binds | phosphorylates | etc.
    evidence_count: int = 0
    composite_confidence: str = "low"
    composite_confidence_score: float = 0.0


class ExtractedInteractionRaw(BaseModel):
    """
    LLM output for a single extracted interaction, before entity resolution.
    Each instance produces one Interaction upsert + one EvidenceRecord insert.
    """
    entity_a: str
    entity_b: str
    interaction_type: InteractionType
    direction: str = "undirected"
    effect: Optional[str] = None
    evidence_type: EvidenceType
    evidence_subtype: Optional[str] = None
    organism: Optional[str] = None
    tissue_cell_type: Optional[str] = None
    condition: Optional[str] = None
    assay_type: Optional[str] = None
    confidence: str                       # high | medium | low
    confidence_score: float = Field(ge=0.0, le=1.0)
    snippet: str = ""
    reasoning: str = ""                   # LLM chain-of-thought (not stored in DB)
