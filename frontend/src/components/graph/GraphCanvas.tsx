import { useRef, useEffect } from 'react'
import Cytoscape from 'cytoscape'
import type { NodeView, EdgeView } from '../../types/api'
import {
  stylesheet,
  fcoseLayout,
  dagreLayout,
  nodeToElement,
  edgeToElement,
} from './cytoscapeConfig'

interface Props {
  nodes: NodeView[]
  edges: EdgeView[]
  seedEntityId: string
  layout?: 'fcose' | 'dagre'
  showUnknown?: boolean
  perturbationScores: Map<string, number> | null
  selectedPath: { nodeIds: string[]; edgeIds: string[] } | null
  onEdgeClick: (edgeId: string) => void
  onNodeClick: (nodeId: string) => void
}

export function GraphCanvas({
  nodes,
  edges,
  seedEntityId,
  layout = 'fcose',
  showUnknown = false,
  perturbationScores,
  selectedPath,
  onEdgeClick,
  onNodeClick,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<Cytoscape.Core | null>(null)
  // Keep latest callbacks in refs so event handlers don't become stale
  const onEdgeClickRef = useRef(onEdgeClick)
  const onNodeClickRef = useRef(onNodeClick)
  onEdgeClickRef.current = onEdgeClick
  onNodeClickRef.current = onNodeClick

  // Mount: create Cytoscape instance once
  useEffect(() => {
    if (!containerRef.current) return
    const cy = Cytoscape({
      container: containerRef.current,
      elements: [],
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      style: stylesheet as any,
      layout: { name: 'preset' },
      userZoomingEnabled: true,
      userPanningEnabled: true,
      minZoom: 0.1,
      maxZoom: 5,
      wheelSensitivity: 2,
    })
    cy.on('tap', 'edge', evt => onEdgeClickRef.current(evt.target.id()))
    cy.on('tap', 'node', evt => onNodeClickRef.current(evt.target.id()))
    cyRef.current = cy
    return () => {
      cy.destroy()
      cyRef.current = null
    }
  }, [])

  // Data change: replace elements and re-layout
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    const visibleEdges = showUnknown
      ? edges
      : edges.filter(e => e.effect !== null && e.effect !== 'unknown' && e.effect !== 'ambiguous')
    const elements = [
      ...nodes.map(n => nodeToElement(n, n.id === seedEntityId)),
      ...visibleEdges.map(edgeToElement),
    ]
    cy.elements().remove()
    cy.add(elements)
    const layoutOpts = layout === 'dagre' ? dagreLayout : fcoseLayout
    cy.layout(layoutOpts).run()
  }, [nodes, edges, seedEntityId, layout, showUnknown])

  // Perturbation scores: imperatively update node data, no re-layout
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    if (!perturbationScores) {
      cy.nodes().removeData('perturbation_score')
      return
    }
    perturbationScores.forEach((score, nodeId) => {
      cy.$id(nodeId).data('perturbation_score', score)
    })
  }, [perturbationScores])

  // Path highlight: dim non-path elements, brighten path elements — no re-layout
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.elements().removeClass('path-highlight path-dim')
    if (!selectedPath) return
    const pathNodeSet = new Set(selectedPath.nodeIds)
    const pathEdgeSet = new Set(selectedPath.edgeIds)
    cy.nodes().forEach(n => {
      if (pathNodeSet.has(n.id())) n.addClass('path-highlight')
      else n.addClass('path-dim')
    })
    cy.edges().forEach(e => {
      if (pathEdgeSet.has(e.id())) e.addClass('path-highlight')
      else e.addClass('path-dim')
    })
  }, [selectedPath])

  return (
    <div className="relative w-full h-full bg-forest-dark">
      <div ref={containerRef} className="w-full h-full" />
      {nodes.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center text-sage text-sm pointer-events-none">
          No interactions found for current filters
        </div>
      )}
    </div>
  )
}
