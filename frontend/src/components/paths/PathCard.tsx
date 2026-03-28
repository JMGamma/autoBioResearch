import type { PathResult } from '../../types/api'

function SignChip({ sign }: { sign: number }) {
  if (sign === 1) return (
    <span className="text-xs px-1.5 py-0.5 rounded bg-green-900/50 text-green-300 border border-green-700/50">
      → activating
    </span>
  )
  if (sign === -1) return (
    <span className="text-xs px-1.5 py-0.5 rounded bg-red-900/50 text-red-300 border border-red-700/50">
      ⊣ inhibitory
    </span>
  )
  return (
    <span className="text-xs px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-300 border border-amber-700/50">
      ? mixed
    </span>
  )
}

interface Props {
  path: PathResult
  index: number
  isSelected: boolean
  onClick: () => void
}

export function PathCard({ path, index, isSelected, onClick }: Props) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left p-3 rounded-lg border transition-colors space-y-2 ${
        isSelected
          ? 'bg-mist/20 border-mist/60'
          : 'bg-forest-mid border-forest-light hover:border-sage/50'
      }`}
    >
      {/* Header row */}
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-sage">#{index + 1} · {path.hop_count} hop{path.hop_count !== 1 ? 's' : ''}</span>
        <div className="flex items-center gap-1.5">
          <SignChip sign={path.net_sign} />
          <span className="text-xs text-mist font-medium">{(path.score * 100).toFixed(1)}%</span>
        </div>
      </div>

      {/* Node chain breadcrumb */}
      <div className="flex flex-wrap items-center gap-1 min-w-0">
        {path.nodes.map((node, i) => (
          <span key={node.id} className="flex items-center gap-1">
            <span className="text-xs text-snow bg-forest px-1.5 py-0.5 rounded truncate max-w-[100px]" title={node.display_name}>
              {node.display_name || node.id.slice(0, 8)}
            </span>
            {i < path.edges.length && (
              <span className="text-xs text-sage/60" title={path.edges[i].effect ?? 'unknown'}>→</span>
            )}
          </span>
        ))}
      </div>
    </button>
  )
}
