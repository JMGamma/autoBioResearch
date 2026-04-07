import { useState, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useEntityDetail } from '../hooks/useEntityDetail'
import { useSubgraph } from '../hooks/useSubgraph'
import { EntitySummaryCard } from '../components/entity/EntitySummaryCard'
import { GraphCanvas } from '../components/graph/GraphCanvas'
import { GraphControls } from '../components/graph/GraphControls'
import { GraphLegend } from '../components/graph/GraphLegend'
import { EvidenceDrawer } from '../components/evidence/EvidenceDrawer'
import { PerturbationPanel } from '../components/perturbation/PerturbationPanel'
import { PathExplorer } from '../components/paths/PathExplorer'
import { Spinner } from '../components/ui/Spinner'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { SearchCombobox } from '../components/search/SearchCombobox'
import type { SubgraphRequest, PathResult } from '../types/api'


export function EntityPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [hops, setHops] = useState(1)
  const [minConfidence, setMinConfidence] = useState(0)
  const [entityTypeFilter, setEntityTypeFilter] = useState<string[]>([])
  const [layout, setLayout] = useState<'fcose' | 'dagre'>('fcose')
  const [showUnknown, setShowUnknown] = useState(false)
  const [edgeLimit, setEdgeLimit] = useState(100)
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)
  const [perturbationScores, setPerturbationScores] = useState<Map<string, number> | null>(null)
  const [selectedPath, setSelectedPath] = useState<PathResult | null>(null)
  const [leftTab, setLeftTab] = useState<'perturbation' | 'paths'>('perturbation')
  const [edgeHintDismissed, setEdgeHintDismissed] = useState(false)
  const [mobileControlsOpen, setMobileControlsOpen] = useState(false)

  const { data: entity, isLoading: entityLoading, error: entityError } = useEntityDetail(id)

  const subgraphReq: SubgraphRequest | null = id
    ? {
        entity_ids: [id],
        hops,
        min_confidence_score: minConfidence > 0 ? minConfidence : undefined,
        entity_type_filter: entityTypeFilter.length > 0 ? entityTypeFilter : null,
        edge_limit: edgeLimit,
      }
    : null

  const { data: subgraph, isLoading: graphLoading } = useSubgraph(subgraphReq)

  const availableEntityTypes = useMemo(() => {
    if (!subgraph) return []
    return [...new Set(subgraph.nodes.map(n => n.entity_type))].sort()
  }, [subgraph])

  const nodes = subgraph?.nodes ?? []
  const edges = subgraph?.edges ?? []

  function handleEdgeClick(edgeId: string) {
    setSelectedEdgeId(edgeId)
    setEdgeHintDismissed(true)
  }

  if (!id) { navigate('/'); return null }

  if (entityLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-forest-dark">
        <Spinner size="lg" />
      </div>
    )
  }

  if (entityError || !entity) {
    return (
      <div className="p-8 max-w-lg mx-auto mt-16 bg-forest-dark min-h-screen">
        <ErrorBanner message={entityError?.message ?? 'Entity not found'} />
        <button
          onClick={() => navigate('/')}
          className="mt-4 text-sm text-mist underline hover:no-underline"
        >
          ← Back to search
        </button>
      </div>
    )
  }

  const showEmptyState = !graphLoading && nodes.length === 0 && subgraph !== undefined
  const showEdgeHint = !edgeHintDismissed && nodes.length > 0 && !graphLoading

  const leftPanel = (
    <div className="flex flex-col gap-3 p-3 overflow-y-auto h-full bg-forest-dark">
      <EntitySummaryCard entity={entity} />
      <GraphControls
        hops={hops}
        onHopsChange={setHops}
        minConfidence={minConfidence}
        onMinConfidenceChange={setMinConfidence}
        entityTypeFilter={entityTypeFilter}
        onEntityTypeFilterChange={setEntityTypeFilter}
        availableEntityTypes={availableEntityTypes}
        layout={layout}
        onLayoutChange={setLayout}
        showUnknown={showUnknown}
        onShowUnknownChange={setShowUnknown}
        edgeLimit={edgeLimit}
        onEdgeLimitChange={setEdgeLimit}
        isLoading={graphLoading}
      />
      {/* Tab strip */}
      <div className="flex border border-forest-light rounded-lg overflow-hidden text-xs" role="tablist">
        {(['perturbation', 'paths'] as const).map(tab => (
          <button
            key={tab}
            role="tab"
            aria-selected={leftTab === tab}
            onClick={() => setLeftTab(tab)}
            className={`flex-1 py-2 capitalize transition-colors ${
              leftTab === tab
                ? 'bg-forest-mid text-snow'
                : 'bg-transparent text-sage hover:text-snow'
            }`}
          >
            {tab === 'perturbation' ? 'Perturbation' : 'Find Path'}
          </button>
        ))}
      </div>

      <div role="tabpanel">
        {leftTab === 'perturbation' && (
          <PerturbationPanel
            seedName={entity.display_name}
            onScoresChange={scores => { setPerturbationScores(scores); setSelectedPath(null) }}
          />
        )}
        {leftTab === 'paths' && (
          <PathExplorer
            sourceEntityName={entity.display_name}
            onPathSelected={path => { setSelectedPath(path); setPerturbationScores(null) }}
          />
        )}
      </div>
    </div>
  )

  return (
    <div className="h-screen flex flex-col overflow-hidden bg-forest-dark">
      {/* Top nav bar */}
      <header className="bg-forest border-b border-forest-light px-4 py-2 flex items-center gap-4 flex-shrink-0">
        <button
          onClick={() => navigate('/')}
          className="text-sage hover:text-snow transition-colors flex items-center gap-1.5 text-sm flex-shrink-0"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path d="M15 18l-6-6 6-6" />
          </svg>
          <span className="font-semibold text-snow">AutoBio<span className="text-sage">Research</span></span>
        </button>
        <div className="flex-1 max-w-md">
          <SearchCombobox
            onSelect={r => navigate(`/entity/${r.id}`)}
            placeholder="Search…"
            size="compact"
          />
        </div>
        <div className="text-xs text-sage flex-shrink-0 flex items-center gap-2">
          {subgraph && `${nodes.length} nodes · ${edges.length} edges`}
          {subgraph?.stats.truncated && (
            <span className="text-amber-400">· results limited to {edges.length} edges</span>
          )}
        </div>
      </header>

      {/* Main layout */}
      <div className="flex flex-1 overflow-hidden relative">
        {/* Left panel — desktop: fixed sidebar; mobile: bottom sheet */}
        <div className={`
          md:w-80 md:flex-shrink-0 md:flex md:flex-col md:border-r md:border-forest-light md:relative md:translate-y-0
          fixed bottom-0 left-0 right-0 z-30 max-h-[65vh] overflow-y-auto
          transition-transform duration-300 ease-in-out
          border-t border-forest-light shadow-2xl
          md:max-h-none md:shadow-none md:border-t-0
          ${mobileControlsOpen ? 'translate-y-0' : 'translate-y-full md:translate-y-0'}
        `}>
          {leftPanel}
        </div>

        {/* Graph canvas */}
        <div className="flex-1 relative overflow-hidden">
          {graphLoading && (
            <div className="absolute top-3 right-3 z-10">
              <Spinner size="sm" />
            </div>
          )}

          {/* Empty state */}
          {showEmptyState && (
            <div className="absolute inset-0 flex items-center justify-center z-10 pointer-events-none">
              <div className="text-center space-y-2 px-8">
                <p className="text-mist text-sm">
                  No interactions found for <span className="text-snow font-medium">{entity.display_name}</span> at {hops} hop{hops !== 1 ? 's' : ''}.
                </p>
                <p className="text-sage text-xs">Try increasing hops or removing filters.</p>
              </div>
            </div>
          )}

          {/* Edge-click discovery hint */}
          {showEdgeHint && (
            <div className="absolute top-3 left-1/2 -translate-x-1/2 z-10 pointer-events-none">
              <span className="bg-forest/80 text-sage text-xs px-3 py-1 rounded-full backdrop-blur-sm border border-forest-light/50">
                Click any edge to see its paper evidence
              </span>
            </div>
          )}

          <GraphCanvas
            nodes={nodes}
            edges={edges}
            seedEntityId={id}
            layout={layout}
            showUnknown={showUnknown}
            perturbationScores={perturbationScores}
            selectedPath={selectedPath ? { nodeIds: selectedPath.nodes.map(n => n.id), edgeIds: selectedPath.edges.map(e => e.id) } : null}
            onEdgeClick={handleEdgeClick}
            onNodeClick={nodeId => { if (nodeId !== id) navigate(`/entity/${nodeId}`) }}
          />
          <GraphLegend
            entityTypes={availableEntityTypes}
            showPerturbation={perturbationScores !== null}
          />

          {/* Mobile controls toggle */}
          <button
            onClick={() => setMobileControlsOpen(v => !v)}
            className="md:hidden absolute bottom-4 left-4 z-20 bg-forest border border-forest-light rounded-full px-4 py-2 text-xs text-snow flex items-center gap-1.5 shadow-lg"
            aria-expanded={mobileControlsOpen}
            aria-label={mobileControlsOpen ? 'Close controls' : 'Open controls'}
          >
            <svg className={`w-3.5 h-3.5 transition-transform ${mobileControlsOpen ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path d="M19 9l-7 7-7-7" />
            </svg>
            Controls
          </button>
        </div>
      </div>

      {/* Mobile backdrop */}
      {mobileControlsOpen && (
        <div
          className="md:hidden fixed inset-0 z-20 bg-black/40"
          onClick={() => setMobileControlsOpen(false)}
        />
      )}

      <EvidenceDrawer
        interactionId={selectedEdgeId}
        onClose={() => setSelectedEdgeId(null)}
      />
    </div>
  )
}
