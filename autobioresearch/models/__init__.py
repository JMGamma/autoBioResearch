from .entity import BiologicalEntity, EntityType, ExtractedEntityRaw
from .interaction import (
    EvidenceRecord,
    ExtractedInteractionRaw,
    Interaction,
    LiteralClaimRecord,
    InteractionType,
    EvidenceType,
    InteractionContext,
)
from .paper import ExtractionResult, ExtractionStatus, FetchStatus, Paper
from .conflict import Conflict, ConflictAnalysisOutput, ConflictStatus, ConflictType, PENALTY_WEIGHTS
from .query import GeneratedQuerySet, QueryType, SearchQuery

# Pydantic v2: resolve forward references that span module boundaries.
# ExtractionResult references ExtractedEntityRaw / ExtractedInteractionRaw from
# sibling modules, so model_rebuild() must be called after all imports are complete.
ExtractionResult.model_rebuild()
Paper.model_rebuild()

__all__ = [
    "BiologicalEntity", "EntityType", "ExtractedEntityRaw",
    "EvidenceRecord", "ExtractedInteractionRaw", "Interaction",
    "LiteralClaimRecord",
    "InteractionType", "EvidenceType", "InteractionContext",
    "ExtractionResult", "ExtractionStatus", "FetchStatus", "Paper",
    "Conflict", "ConflictAnalysisOutput", "ConflictStatus", "ConflictType", "PENALTY_WEIGHTS",
    "GeneratedQuerySet", "QueryType", "SearchQuery",
]
