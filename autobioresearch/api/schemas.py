"""
Public Pydantic response models for the AutoBioResearch API.

These are intentionally separate from the internal domain models so the public
API contract can evolve independently of the internal data layer.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class EntitySearchResult(BaseModel):
    id: str
    display_name: str
    canonical_name: str
    entity_type: str
    organism: Optional[str]
    paper_count: int
    matching_synonym: Optional[str]


class EntityDetail(EntitySearchResult):
    degree_in: int
    degree_out: int
    degree_total: int
    evidence_total: int
    has_conflicts: bool
    open_conflict_count: int


class NodeView(BaseModel):
    id: str
    display_name: str
    canonical_name: str
    entity_type: str
    organism: Optional[str]
    paper_count: int
    has_conflicts: bool = False


class EdgeView(BaseModel):
    id: str
    source: str
    target: str
    interaction_type: str
    direction: str
    effect: Optional[str]
    composite_confidence: str
    composite_confidence_score: float
    evidence_count: int


class SubgraphRequest(BaseModel):
    entity_ids: list[str]
    hops: int = Field(default=1, ge=1, le=3)
    edge_limit: int = Field(default=50, ge=1, le=200)
    entity_type_filter: Optional[list[str]] = None
    min_confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)


class SubgraphResponse(BaseModel):
    nodes: list[NodeView]
    edges: list[EdgeView]
    stats: dict[str, Any]


class PerturbationRequest(BaseModel):
    entity: str
    mode: str = "suppress"  # "suppress" | "promote"
    depth: int = Field(default=3, ge=1, le=4)


class PerturbationResponse(BaseModel):
    schema_version: str = "1.0"
    query: dict[str, Any]
    seed: dict[str, Any]
    affected: list[dict[str, Any]]
    excluded_edges: list[dict[str, Any]]
    stats: dict[str, Any]


class EvidenceItem(BaseModel):
    id: str
    evidence_type: str
    confidence: str
    confidence_score: float
    snippet: Optional[str]
    organism: Optional[str]
    tissue_cell_type: Optional[str]
    condition: Optional[str]
    paper_id: Optional[str]
    paper_title: Optional[str]
    paper_doi: Optional[str]
    paper_year: Optional[int]
    paper_journal: Optional[str]


class EvidenceResponse(BaseModel):
    interaction_id: str
    interaction_type: Optional[str] = None
    entity_a_name: Optional[str] = None
    entity_b_name: Optional[str] = None
    evidence: list[EvidenceItem]


class ConflictSummary(BaseModel):
    id: str
    conflict_type: str
    conflict_axis: str
    status: str
    interaction_a_entity_a: str
    interaction_a_entity_b: str
    interaction_a_effect: Optional[str]
    interaction_a_type: str
    interaction_b_entity_a: str
    interaction_b_entity_b: str
    interaction_b_effect: Optional[str]
    interaction_b_type: str
    llm_analysis: Optional[str] = None


class ConflictsResponse(BaseModel):
    entity_id: str
    conflicts: list[ConflictSummary]


class StatsResponse(BaseModel):
    n_entities: int
    n_interactions: int
    n_evidence: int
    n_open_conflicts: int
    n_papers: int
    last_score: Optional[float]
    last_cycle_timestamp: Optional[str]


# ── Phase 3: Path exploration ──────────────────────────────────────────────────

class PathNode(BaseModel):
    id: str
    display_name: str
    entity_type: str


class PathEdge(BaseModel):
    id: str
    source: str
    target: str
    effect: Optional[str]
    confidence_score: float
    direction: str


class PathResult(BaseModel):
    nodes: list[PathNode]
    edges: list[PathEdge]
    score: float
    hop_count: int
    net_sign: int  # +1 activating, -1 inhibitory, 0 mixed/unknown


class PathsRequest(BaseModel):
    source_entity: str
    target_entity: str
    max_hops: int = Field(default=4, ge=1, le=5)
    min_confidence: float = Field(default=0.3, ge=0.0, le=1.0)
    max_paths: int = Field(default=20, ge=1, le=50)


class PathsResponse(BaseModel):
    source: dict[str, Any]
    target: dict[str, Any]
    paths: list[PathResult]
    stats: dict[str, Any]
