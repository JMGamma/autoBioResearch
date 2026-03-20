from enum import StrEnum
from typing import Optional, TYPE_CHECKING
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .entity import ExtractedEntityRaw
    from .interaction import ExtractedInteractionRaw


class FetchStatus(StrEnum):
    PENDING = "pending"
    ABSTRACT_ONLY = "abstract_only"
    FULL_TEXT_AVAILABLE = "full_text_available"  # pmc_id present, fetchable on demand
    FAILED = "failed"


class ExtractionStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class Paper(BaseModel):
    id: str                                  # prefixed: "pmid:12345678"
    source: str                              # pubmed | semantic_scholar | arxiv
    title: Optional[str] = None
    abstract: Optional[str] = None           # retained permanently
    # full_text is NEVER stored in the DB — fetched transiently during extraction only
    authors: list[str] = Field(default_factory=list)
    journal: Optional[str] = None
    year: Optional[int] = None
    doi: Optional[str] = None
    pmc_id: Optional[str] = None
    fetch_status: FetchStatus = FetchStatus.PENDING
    extraction_status: ExtractionStatus = ExtractionStatus.PENDING
    extraction_error: Optional[str] = None
    query_ids: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    paper_id: str
    entities: list["ExtractedEntityRaw"]
    interactions: list["ExtractedInteractionRaw"]
    extraction_notes: str = ""
    token_usage: dict[str, int] = Field(default_factory=dict)
