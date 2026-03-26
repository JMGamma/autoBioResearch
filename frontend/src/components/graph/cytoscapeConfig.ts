import Cytoscape from 'cytoscape'
import type { NodeView, EdgeView } from '../../types/api'

// ─── Color maps ───────────────────────────────────────────────────────────────
// Light, readable colors against the dark forest/olive canvas
export const ENTITY_TYPE_COLORS: Record<string, string> = {
  gene:      '#93c5fd', // blue-300
  protein:   '#7dd3fc', // sky-300
  molecule:  '#6ee7b7', // emerald-300
  pathway:   '#fcd34d', // amber-300
  disease:   '#fca5a5', // red-300
  cell_type: '#c4b5fd', // violet-300
  organism:  '#76abae', // accent (p3)
  default:   '#a8c5c8', // accent-light
}

export const EFFECT_EDGE_COLORS: Record<string, string> = {
  activates:  '#4ade80', // green-400
  promotes:   '#4ade80',
  inhibits:   '#f87171', // red-400
  suppresses: '#f87171',
  unknown:    '#4a5568', // slate
  ambiguous:  '#fbbf24', // amber-400
}

function nodeColor(ele: Cytoscape.NodeSingular): string {
  const t = (ele.data('entity_type') as string | undefined)?.toLowerCase() ?? ''
  return ENTITY_TYPE_COLORS[t] ?? ENTITY_TYPE_COLORS.default
}

function edgeColor(ele: Cytoscape.EdgeSingular): string {
  const e = (ele.data('effect') as string | undefined)?.toLowerCase() ?? 'unknown'
  return EFFECT_EDGE_COLORS[e] ?? EFFECT_EDGE_COLORS.unknown
}

function nodeSize(ele: Cytoscape.NodeSingular): number {
  const pc = (ele.data('paper_count') as number | undefined) ?? 0
  return Math.max(30, Math.min(60, 20 + pc * 2))
}

function edgeWidth(ele: Cytoscape.EdgeSingular): number {
  const ec = (ele.data('evidence_count') as number | undefined) ?? 1
  return Math.max(1, Math.min(6, ec * 0.5))
}

function edgeOpacity(ele: Cytoscape.EdgeSingular): number {
  const cs = (ele.data('confidence_score') as number | undefined) ?? 0.5
  return 0.4 + cs * 0.6
}

// ─── Stylesheet ───────────────────────────────────────────────────────────────
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const stylesheet: any[] = [
  {
    selector: 'node',
    style: {
      'background-color': nodeColor as unknown as string,
      'label': 'data(label)',
      'font-size': 11,
      'text-valign': 'bottom',
      'text-halign': 'center',
      'text-margin-y': 4,
      'color': '#eeeeee',
      'text-outline-width': 2,
      'text-outline-color': '#222831',
      'width': nodeSize as unknown as string,
      'height': nodeSize as unknown as string,
      'border-width': 2,
      'border-color': '#31363f',
    },
  },
  {
    selector: 'node[?is_seed]',
    style: {
      'border-width': 4,
      'border-color': '#eeeeee',
      'font-size': 13,
      'font-weight': 'bold',
    },
  },
  {
    selector: 'node[?has_conflicts]',
    style: {
      'border-color': '#f87171',
      'border-width': 3,
    },
  },
  {
    selector: 'node:selected',
    style: {
      'border-color': '#76abae',
      'border-width': 4,
    },
  },
  {
    selector: 'edge',
    style: {
      'line-color': edgeColor as unknown as string,
      'target-arrow-color': edgeColor as unknown as string,
      'target-arrow-shape': 'triangle',
      'curve-style': 'bezier',
      'width': edgeWidth as unknown as string,
      'opacity': edgeOpacity as unknown as string,
      'label': '',
      'font-size': 9,
      'text-rotation': 'autorotate',
      'color': '#76abae',
    },
  },
  {
    selector: 'edge:selected',
    style: {
      'line-color': '#76abae',
      'target-arrow-color': '#76abae',
      'width': 4,
      'opacity': 1,
      'label': 'data(label)',
    },
  },
]

// ─── Layouts ──────────────────────────────────────────────────────────────────
export const fcoseLayout: Cytoscape.LayoutOptions = {
  name: 'fcose',
  // @ts-expect-error cytoscape-fcose options not in base types
  quality: 'proof',
  animate: true,
  animationDuration: 600,
  fit: true,
  padding: 60,
  randomize: true,
  nodeSeparation: 120,
  idealEdgeLength: () => 150,
  nodeRepulsion: () => 18000,
  edgeElasticity: () => 0.45,
  gravity: 0.25,
  gravityRange: 3.8,
  numIter: 3500,
  tile: true,
  tilingPaddingVertical: 20,
  tilingPaddingHorizontal: 20,
}

export const dagreLayout: Cytoscape.LayoutOptions = {
  name: 'dagre',
  // @ts-expect-error cytoscape-dagre options not in base types
  rankDir: 'LR',
  nodeSep: 60,
  rankSep: 100,
  animate: true,
  animationDuration: 300,
  fit: true,
  padding: 40,
}

// ─── Element mapping ──────────────────────────────────────────────────────────
export function nodeToElement(n: NodeView, isSeed: boolean): Cytoscape.ElementDefinition {
  return {
    data: {
      id: n.id,
      label: n.display_name,
      entity_type: n.entity_type,
      organism: n.organism ?? '',
      paper_count: n.paper_count,
      has_conflicts: n.has_conflicts,
      is_seed: isSeed,
    },
  }
}

export function edgeToElement(e: EdgeView): Cytoscape.ElementDefinition {
  return {
    data: {
      id: e.id,
      source: e.source,
      target: e.target,
      interaction_type: e.interaction_type,
      direction: e.direction,
      effect: e.effect ?? 'unknown',
      confidence_score: e.composite_confidence_score,
      evidence_count: e.evidence_count,
      label: e.interaction_type,
    },
  }
}
