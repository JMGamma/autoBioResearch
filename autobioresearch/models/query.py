from enum import StrEnum
from typing import Optional
from pydantic import BaseModel, Field
import uuid


class QueryType(StrEnum):
    INITIAL = "initial"
    CONFLICT_RESOLUTION = "conflict_resolution"
    ENTITY_EXPANSION = "entity_expansion"
    GAP_FILLING = "gap_filling"


class SearchQuery(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    query_text: str
    source_api: str                          # pubmed | semantic_scholar | arxiv
    query_type: QueryType
    origin: str                              # "seed" | "conflict_id:<uuid>" | "entity_id:<uuid>"
    status: str = "pending"                  # pending | running | done | failed
    papers_found: int = 0
    papers_new: int = 0
    executed_at: Optional[str] = None


class GeneratedQuerySet(BaseModel):
    """LLM output when generating conflict-resolution or gap-filling queries."""
    summary: str
    queries: list[dict]                      # [{query_text, source_api, reasoning}]
