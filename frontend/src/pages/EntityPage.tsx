import { useState, useMemo, useEffect } from 'react'
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
import { SearchBar } from '../components/search/SearchBar'
import { SearchResults } from '../components/search/SearchResults'
import { useEntitySearch } from '../hooks/useEntitySearch'
import type { SubgraphRequest, PathResult } from '../types/api'

function useDebounce(value: string, delay: number) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return debounced
}

export function EntityPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [hops, setHops] = useState(1)
  const [minConfidence, setMinConfidence] = useState(0)
  const [entityTypeFilter, setEntityTypeFilter] = useState<string[]>([])
  const [layout, setLayout] = useState<'fcose' | 'dagre'>('fcose')
  const [showUnknown, setShowUnknown] = useState(false)
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null)
  const [perturbationScores, setPerturbationScores] = useState<Map<string, number> | null>(null)
  const [selectedPath, setSelectedPath] = useState<PathResult | null>(null)
  const [leftTab, setLeftTab] = useState<'perturbation' | 'paths'>('perturbation')

  const [searchQuery, setSearchQuery] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const debouncedSearch = useDebounce(searchQuery, 250)
  const { data: searchResults, isLoading: searchLoading, error: searchError } = useEntitySearch(debouncedSearch)

  const { data: entity, isLoading: entityLoading, error: entityError } = useEntityDetail(id)

  const subgraphReq: SubgraphRequest | null = id
    ? {
        entity_ids: [id],
        hops,
        min_confidence_score: minConfidence > 0 ? minConfidence : undefined,
        entity_type_filter: entityTypeFilter.length > 0 ? entityTypeFilter : null,
        edge_limit: 100,
      }
    : null

  const { data: subgraph, isLoading: graphLoading } = useSubgraph(subgraphReq)

  const availableEntityTypes = useMemo(() => {
    if (!subgraph) return []
    return [...new Set(subgraph.nodes.map(n => n.entity_type))].sort()
  }, [subgraph])

  const nodes = subgraph?.nodes ?? []
  const edges = subgraph?.edges ?? []

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
        <div className="relative flex-1 max-w-md">
          <SearchBar
            value={searchQuery}
            onChange={v => { setSearchQuery(v); setSearchOpen(true) }}
            placeholder="Search…"
          />
          {searchOpen && (
            <SearchResults
              results={searchResults}
              isLoading={searchLoading}
              error={searchError}
              query={debouncedSearch}
              onSelect={r => { setSearchOpen(false); setSearchQuery(''); navigate(`/entity/${r.id}`) }}
            />
          )}
        </div>
        <div className="text-xs text-sage flex-shrink-0">
          {subgraph && `${nodes.length} nodes · ${edges.length} edges`}
        </div>
      </header>

      {/* Main layout */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left panel */}
        <div className="w-80 flex-shrink-0 flex flex-col gap-3 p-3 overflow-y-auto bg-forest-dark border-r border-forest-light">
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
            isLoading={graphLoading}
          />
          {/* Tab strip */}
          <div className="flex border border-forest-light rounded-lg overflow-hidden text-xs">
            {(['perturbation', 'paths'] as const).map(tab => (
              <button
                key={tab}
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

        {/* Graph canvas */}
        <div className="flex-1 relative overflow-hidden">
          {graphLoading && (
            <div className="absolute top-3 right-3 z-10">
              <Spinner size="sm" />
            </div>
          )}
          <GraphCanvas
            nodes={nodes}
            edges={edges}
            seedEntityId={id!}
            layout={layout}
            showUnknown={showUnknown}
            perturbationScores={perturbationScores}
            selectedPath={selectedPath ? { nodeIds: selectedPath.nodes.map(n => n.id), edgeIds: selectedPath.edges.map(e => e.id) } : null}
            onEdgeClick={setSelectedEdgeId}
            onNodeClick={nodeId => { if (nodeId !== id) navigate(`/entity/${nodeId}`) }}
          />
          <GraphLegend
            entityTypes={availableEntityTypes}
            showPerturbation={perturbationScores !== null}
          />
        </div>
      </div>

      <EvidenceDrawer
        interactionId={selectedEdgeId}
        onClose={() => setSelectedEdgeId(null)}
      />
    </div>
  )
}
