import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { AffectedEntity } from '../../types/api'
import { usePerturbation } from '../../hooks/usePerturbation'
import { Spinner } from '../ui/Spinner'
import { ErrorBanner } from '../ui/ErrorBanner'

function ScoreRow({ entity, onNavigate }: { entity: AffectedEntity; onNavigate: () => void }) {
  const [expanded, setExpanded] = useState(false)
  const score = entity.perturbation_score
  const hasTrail = entity.contributing_interactions.length > 0

  return (
    <div className="rounded-lg border border-transparent hover:border-forest-light transition-colors">
      <div className="flex items-center gap-1">
        <button
          onClick={onNavigate}
          className="flex-1 text-left flex items-center justify-between gap-2 px-2 py-1.5"
        >
          <div className="min-w-0">
            <span className="text-sm text-snow truncate block">{entity.name}</span>
            <span className="text-xs text-sage">depth {entity.hop_depth}</span>
          </div>
          <span className={`text-sm font-bold flex-shrink-0 ${score > 0 ? 'text-green-400' : 'text-red-400'}`}>
            {score > 0 ? '+' : ''}{score.toFixed(3)}
          </span>
        </button>
        {hasTrail && (
          <button
            onClick={() => setExpanded(v => !v)}
            className="px-1.5 py-1.5 text-sage hover:text-snow transition-colors flex-shrink-0"
            title="Show explanation trail"
          >
            <svg className={`w-3.5 h-3.5 transition-transform ${expanded ? 'rotate-180' : ''}`}
              fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path d="M19 9l-7 7-7-7" />
            </svg>
          </button>
        )}
      </div>
      {expanded && hasTrail && (
        <div className="px-2 pb-2 space-y-1">
          {entity.contributing_interactions.map((ci, i) => (
            <div key={i} className="flex items-center gap-1.5 text-xs text-sage/80 pl-1 border-l border-forest-light">
              <span className={`font-mono flex-shrink-0 ${ci.sign > 0 ? 'text-green-400/80' : 'text-red-400/80'}`}>
                {ci.sign > 0 ? '▲' : '▼'}
              </span>
              <span className="truncate">{ci.from_entity}</span>
              <span className="text-sage/50 flex-shrink-0">→</span>
              <span className="truncate italic">{ci.effect ?? 'unknown'}</span>
              <span className="text-sage/50 flex-shrink-0 ml-auto">{(ci.confidence_score * 100).toFixed(0)}%</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function PerturbationPanel({ seedName, onScoresChange }: {
  seedName: string
  onScoresChange: (scores: Map<string, number> | null) => void
}) {
  const navigate = useNavigate()
  const [localMode, setLocalMode] = useState<'promote' | 'suppress'>('suppress')
  const [depth, setDepth] = useState(3)
  const { mutate, data, isPending, error, reset } = usePerturbation()

  function handleModeChange(m: 'promote' | 'suppress') { setLocalMode(m); reset(); onScoresChange(null) }
  function handleDepthChange(d: number) { setDepth(d); reset(); onScoresChange(null) }

  function handleRun() {
    mutate({ entity: seedName, mode: localMode, depth }, {
      onSuccess: result => {
        onScoresChange(new Map(result.affected.map(a => [a.entity_id, a.perturbation_score])))
      },
    })
  }

  const activeMode = data?.query.mode ?? localMode
  const topAffected = data
    ? [...data.affected].sort((a, b) => Math.abs(b.perturbation_score) - Math.abs(a.perturbation_score)).slice(0, 10)
    : []

  const activeBtn = (m: 'promote' | 'suppress') =>
    activeMode === m
      ? m === 'promote' ? 'bg-green-800/60 border-green-500/60 text-green-200' : 'bg-red-800/60 border-red-500/60 text-red-200'
      : 'bg-transparent border-forest-light text-mist hover:border-sage'

  return (
    <div className="bg-forest-mid border border-forest-light rounded-xl p-4 space-y-4">
      <div>
        <h3 className="text-xs font-semibold text-sage uppercase tracking-wider">Perturbation Preview</h3>
        <p className="text-xs text-sage/70 mt-0.5">Evidence-weighted propagation</p>
      </div>

      <div className="flex gap-2">
        {(['promote', 'suppress'] as const).map(m => (
          <button key={m} onClick={() => handleModeChange(m)}
            className={`flex-1 py-2 text-sm rounded-lg border capitalize transition-colors ${activeBtn(m)}`}>
            {m}
          </button>
        ))}
      </div>

      <div>
        <label className="text-xs text-sage mb-1 block">
          Depth: <span className="text-mist font-medium">{depth} hops</span>
        </label>
        <input type="range" min={1} max={4} step={1} value={depth}
          onChange={e => handleDepthChange(parseInt(e.target.value))} className="w-full" />
      </div>

      <div className="flex gap-2">
        <button onClick={handleRun} disabled={isPending}
          className="flex-1 flex items-center justify-center gap-2 py-2 text-sm bg-mist/20 hover:bg-mist/30 border border-mist/50 disabled:opacity-50 text-snow rounded-lg transition-colors">
          {isPending && <Spinner size="sm" />}
          Run Preview
        </button>
        {data && (
          <button onClick={() => { reset(); onScoresChange(null) }}
            className="px-3 py-2 text-sm bg-transparent hover:bg-forest border border-forest-light text-mist rounded-lg transition-colors">
            Clear
          </button>
        )}
      </div>

      {error && <ErrorBanner message={error.message} />}

      {data && (
        <div className="space-y-1">
          <p className="text-xs text-sage font-medium">
            Predicted response ({data.stats.n_affected} affected):
          </p>
          {topAffected.map(a => (
            <ScoreRow key={a.entity_id} entity={a} onNavigate={() => navigate(`/entity/${a.entity_id}`)} />
          ))}
          {data.stats.n_excluded_edges > 0 && (
            <p className="text-xs text-sage/60 pt-1">
              {data.stats.n_excluded_edges} edge{data.stats.n_excluded_edges !== 1 ? 's' : ''} excluded
            </p>
          )}
        </div>
      )}
    </div>
  )
}
