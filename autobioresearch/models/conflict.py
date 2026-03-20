from enum import StrEnum
from typing import Optional
from pydantic import BaseModel, Field
import uuid


class ConflictType(StrEnum):
    TRUE_CONFLICT = "true_conflict"           # same context, opposite claims
    CONTEXT_DEPENDENT = "context_dependent"   # different organisms/conditions explain divergence
    AMBIGUOUS = "ambiguous"                   # unclear without more data


class ConflictStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"                   # determined not a real conflict
    INVESTIGATING = "investigating"           # resolution queries generated, awaiting data


PENALTY_WEIGHTS: dict[ConflictType, float] = {
    ConflictType.TRUE_CONFLICT: 1.0,
    ConflictType.AMBIGUOUS: 0.5,
    ConflictType.CONTEXT_DEPENDENT: 0.2,
}


class Conflict(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    interaction_a_id: str
    interaction_b_id: str
    conflict_type: ConflictType
    conflict_axis: str                        # what dimension conflicts: effect|direction|etc.
    context_difference: dict = Field(default_factory=dict)
    status: ConflictStatus = ConflictStatus.OPEN
    resolution_paper_id: Optional[str] = None
    resolution_note: Optional[str] = None
    llm_analysis: Optional[str] = None
    generated_query_ids: list[str] = Field(default_factory=list)
    penalty_weight: float = 1.0


class ConflictAnalysisOutput(BaseModel):
    """Structured LLM output for conflict classification."""
    conflict_type: ConflictType
    conflict_axis: str
    context_difference: dict
    is_genuine_conflict: bool
    reasoning: str
    suggested_queries: list[str]
    penalty_weight: float = Field(ge=0.0, le=1.0)
