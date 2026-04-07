// ─── Search ───────────────────────────────────────────────────────────────────
export interface EntitySearchResult {
  id: string
  display_name: string
  canonical_name: string
  entity_type: string
  organism: string | null
  paper_count: number
  matching_synonym: string | null
}

// ─── Entity detail ────────────────────────────────────────────────────────────
export interface EntityDetail extends EntitySearchResult {
  degree_in: number
  degree_out: number
  degree_total: number
  evidence_total: number
  has_conflicts: boolean
  open_conflict_count: number
}

// ─── Graph ────────────────────────────────────────────────────────────────────
export interface NodeView {
  id: string
  display_name: string
  canonical_name: string
  entity_type: string
  organism: string | null
  paper_count: number
  has_conflicts: boolean
}

export interface EdgeView {
  id: string
  source: string
  target: string
  interaction_type: string
  direction: string // "A_TO_B" | "B_TO_A" | "BIDIRECTIONAL" | "UNDIRECTED"
  effect: string | null // "activates" | "inhibits" | "ambiguous" | null
  composite_confidence: string
  composite_confidence_score: number
  evidence_count: number
}

export interface SubgraphRequest {
  entity_ids: string[]
  hops?: number
  edge_limit?: number
  entity_type_filter?: string[] | null
  min_confidence_score?: number
}

export interface SubgraphResponse {
  nodes: NodeView[]
  edges: EdgeView[]
  stats: {
    n_nodes: number
    n_edges: number
    max_hops_reached: number
    truncated: boolean
  }
}

// ─── Conflicts ────────────────────────────────────────────────────────────────
export interface ConflictSummary {
  id: string
  conflict_type: string
  conflict_axis: string
  status: string
  interaction_a_entity_a: string
  interaction_a_entity_b: string
  interaction_a_effect: string | null
  interaction_a_type: string
  interaction_b_entity_a: string
  interaction_b_entity_b: string
  interaction_b_effect: string | null
  interaction_b_type: string
  llm_analysis: string | null
}

export interface ConflictsResponse {
  entity_id: string
  conflicts: ConflictSummary[]
}

// ─── Evidence ─────────────────────────────────────────────────────────────────
export interface EvidenceItem {
  id: string
  evidence_type: string
  confidence: string
  confidence_score: number
  snippet: string | null
  organism: string | null
  tissue_cell_type: string | null
  condition: string | null
  paper_id: string | null
  paper_title: string | null
  paper_doi: string | null
  paper_year: number | null
  paper_journal: string | null
}

export interface EvidenceResponse {
  interaction_id: string
  interaction_type: string | null
  entity_a_name: string | null
  entity_b_name: string | null
  evidence: EvidenceItem[]
}

// ─── Perturbation ─────────────────────────────────────────────────────────────
export interface PerturbationRequest {
  entity: string // name or synonym string, resolved server-side — NOT an ID
  mode: 'promote' | 'suppress'
  depth: number
}

export interface ContributingInteraction {
  interaction_id: string
  from_entity: string
  effect: string | null
  sign: number  // +1 activating, -1 inhibitory
  confidence_score: number
}

export interface AffectedEntity {
  entity_id: string
  name: string
  perturbation_score: number
  hop_depth: number
  contributing_interactions: ContributingInteraction[]
}

export interface PerturbationResponse {
  schema_version: string
  query: {
    entity: string
    entity_id: string
    entity_resolved: string
    mode: 'promote' | 'suppress'
    depth: number
    cache_hit: boolean
  }
  seed: { entity_id: string; name: string; perturbation_score: number }
  affected: AffectedEntity[]
  excluded_edges: Array<{
    interaction_id: string
    entity_a: string
    entity_b: string
    effect: string | null
    reason: string
  }>
  stats: {
    n_affected: number
    n_excluded_edges: number
    duration_ms: number
  }
}

// ─── Paths ────────────────────────────────────────────────────────────────────
export interface PathNode {
  id: string
  display_name: string
  entity_type: string
}

export interface PathEdge {
  id: string
  source: string
  target: string
  effect: string | null
  confidence_score: number
  direction: string
}

export interface PathResult {
  nodes: PathNode[]
  edges: PathEdge[]
  score: number
  hop_count: number
  net_sign: number // +1 activating, -1 inhibitory, 0 mixed/unknown
}

export interface PathsRequest {
  source_entity: string
  target_entity: string
  max_hops: number
  min_confidence: number
  max_paths: number
}

export interface PathsResponse {
  source: { id: string; display_name: string }
  target: { id: string; display_name: string }
  paths: PathResult[]
  stats: { paths_found: number; max_hops_searched: number; duration_ms: number }
}

// ─── Stats ────────────────────────────────────────────────────────────────────
export interface StatsResponse {
  n_entities: number
  n_interactions: number
  n_evidence: number
  n_open_conflicts: number
  n_papers: number
  last_score: number | null
  last_cycle_timestamp: string | null
}
