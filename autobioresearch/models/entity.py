from enum import StrEnum
from typing import Optional
from pydantic import BaseModel, Field
import uuid


class EntityType(StrEnum):
    PROTEIN = "protein"
    GENE = "gene"
    MOLECULE = "molecule"         # small molecule, drug
    METABOLITE = "metabolite"
    RNA = "rna"                   # mRNA, lncRNA, miRNA, etc.
    PATHWAY = "pathway"
    PHENOTYPE = "phenotype"
    DISEASE = "disease"
    CELL_TYPE = "cell_type"
    ORGANISM = "organism"
    COMPLEX = "complex"           # macromolecular complex
    UNKNOWN = "unknown"


class BiologicalEntity(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    canonical_name: str
    display_name: str
    entity_type: EntityType
    synonyms: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)
    organism: Optional[str] = None
    description: Optional[str] = None
    paper_count: int = 0


class ExtractedEntityRaw(BaseModel):
    """Direct LLM output before normalization."""
    name: str
    entity_type: EntityType
    synonyms: list[str] = Field(default_factory=list)
    organism: Optional[str] = None
