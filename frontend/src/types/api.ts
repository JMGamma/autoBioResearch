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
  }
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
  evidence: EvidenceItem[]
}

// ─── Perturbation ─────────────────────────────────────────────────────────────
export interface PerturbationRequest {
  entity: string // name or synonym string, resolved server-side — NOT an ID
  mode: 'promote' | 'suppress'
  depth: number
}

export interface AffectedEntity {
  entity_id: string
  name: string
  perturbation_score: number
  contributing_paths: string[][]
  min_hop_depth: number
}

export interface PerturbationResponse {
  query: {
    entity: string
    entity_id: string
    entity_resolved: string
    mode: 'promote' | 'suppress'
    depth: number
  }
  seed: { id: string; name: string }
  affected: AffectedEntity[]
  excluded_edges: Array<{ id: string; reason: string }>
  stats: {
    n_affected: number
    n_excluded_edges: number
    duration_ms: number
  }
}

// ─── Stats ────────────────────────────────────────────────────────────────────
export interface StatsResponse {
  n_entities: number
  n_interactions: number
  n_evidence: number
  n_open_conflicts: number
  last_score: number | null
  last_cycle_timestamp: string | null
}
