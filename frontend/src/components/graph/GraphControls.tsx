interface Props {
  hops: number
  onHopsChange: (h: number) => void
  minConfidence: number
  onMinConfidenceChange: (v: number) => void
  entityTypeFilter: string[]
  onEntityTypeFilterChange: (types: string[]) => void
  availableEntityTypes: string[]
  layout: 'fcose' | 'dagre'
  onLayoutChange: (l: 'fcose' | 'dagre') => void
  showUnknown: boolean
  onShowUnknownChange: (v: boolean) => void
  edgeLimit: number
  onEdgeLimitChange: (v: number) => void
  isLoading: boolean
}

export function GraphControls({
  hops, onHopsChange,
  minConfidence, onMinConfidenceChange,
  entityTypeFilter, onEntityTypeFilterChange,
  availableEntityTypes,
  layout, onLayoutChange,
  showUnknown, onShowUnknownChange,
  edgeLimit, onEdgeLimitChange,
  isLoading,
}: Props) {
  function toggleEntityType(type: string) {
    onEntityTypeFilterChange(
      entityTypeFilter.includes(type)
        ? entityTypeFilter.filter(t => t !== type)
        : [...entityTypeFilter, type]
    )
  }

  const activeBtn = 'bg-mist/20 border-mist text-snow'
  const inactiveBtn = 'bg-transparent border-forest-light text-mist hover:border-sage'

  return (
    <div className="bg-forest-mid border border-forest-light rounded-xl p-4 space-y-4">
      <h3 className="text-xs font-semibold text-sage uppercase tracking-wider">Graph Controls</h3>

      <div>
        <label className="text-xs text-sage mb-2 block">Neighborhood depth</label>
        <div className="flex gap-2">
          {[1, 2, 3].map(h => (
            <button key={h} onClick={() => onHopsChange(h)}
              className={`flex-1 py-1.5 text-sm rounded-lg border transition-colors ${hops === h ? activeBtn : inactiveBtn}`}>
              {h}-hop
            </button>
          ))}
        </div>
      </div>

      <div>
        <label className="text-xs text-sage mb-1 block">
          Min confidence: <span className="text-mist font-medium">{minConfidence.toFixed(2)}</span>
        </label>
        <input type="range" min={0} max={1} step={0.05}
          value={minConfidence}
          onChange={e => onMinConfidenceChange(parseFloat(e.target.value))}
          className="w-full" />
      </div>

      {availableEntityTypes.length > 0 && (
        <div>
          <label className="text-xs text-sage mb-2 block">Filter by type</label>
          <div className="flex flex-wrap gap-1.5">
            {availableEntityTypes.map(t => (
              <button key={t} onClick={() => toggleEntityType(t)}
                className={`text-xs px-2 py-1 rounded-md border transition-colors ${
                  entityTypeFilter.includes(t) ? activeBtn : inactiveBtn
                }`}>
                {t}
              </button>
            ))}
          </div>
        </div>
      )}

      <div>
        <label className="text-xs text-sage mb-2 block">
          Edge limit: <span className="text-mist font-medium">{edgeLimit}</span>
        </label>
        <div className="flex gap-2">
          {[50, 100, 150, 200].map(v => (
            <button key={v} onClick={() => onEdgeLimitChange(v)}
              className={`flex-1 py-1.5 text-xs rounded-lg border transition-colors ${edgeLimit === v ? activeBtn : inactiveBtn}`}>
              {v}
            </button>
          ))}
        </div>
      </div>

      <div className="flex items-center justify-between">
        <span className="text-xs text-sage">Show unknown / ambiguous</span>
        <button
          onClick={() => onShowUnknownChange(!showUnknown)}
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
            showUnknown ? 'bg-mist/60' : 'bg-forest-light/40'
          }`}
        >
          <span className={`inline-block h-3.5 w-3.5 rounded-full bg-snow shadow transition-transform ${
            showUnknown ? 'translate-x-4' : 'translate-x-1'
          }`} />
        </button>
      </div>

      <div>
        <label className="text-xs text-sage mb-2 block">Layout</label>
        <div className="flex gap-2">
          {(['fcose', 'dagre'] as const).map(l => (
            <button key={l} onClick={() => onLayoutChange(l)}
              className={`flex-1 py-1.5 text-xs rounded-lg border transition-colors ${
                layout === l ? activeBtn : inactiveBtn
              }`}>
              {l === 'fcose' ? 'Force-directed' : 'Causal chain'}
            </button>
          ))}
        </div>
      </div>

      {isLoading && <p className="text-xs text-sage text-center">Loading graph…</p>}
    </div>
  )
}
