"""
Public Pydantic response models for the AutoBioResearch API.

These are intentionally separate from the internal domain models so the public
API contract can evolve independently of the internal data layer.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


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
    hops: int = 1
    edge_limit: int = 50
    entity_type_filter: Optional[list[str]] = None
    min_confidence_score: float = 0.0


class SubgraphResponse(BaseModel):
    nodes: list[NodeView]
    edges: list[EdgeView]
    stats: dict[str, Any]


class PerturbationRequest(BaseModel):
    entity: str
    mode: str = "suppress"  # "suppress" | "promote"
    depth: int = 3


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
    evidence: list[EvidenceItem]


class StatsResponse(BaseModel):
    n_entities: int
    n_interactions: int
    n_evidence: int
    n_open_conflicts: int
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
    max_hops: int = 4
    min_confidence: float = 0.3
    max_paths: int = 20


class PathsResponse(BaseModel):
    source: dict[str, Any]
    target: dict[str, Any]
    paths: list[PathResult]
    stats: dict[str, Any]
