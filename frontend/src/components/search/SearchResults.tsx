import type { EntitySearchResult } from '../../types/api'
import { EntityBadge } from '../entity/EntityBadge'
import { Spinner } from '../ui/Spinner'

interface Props {
  results: EntitySearchResult[] | undefined
  isLoading: boolean
  error?: Error | null
  query: string
  onSelect: (result: EntitySearchResult) => void
}

export function SearchResults({ results, isLoading, error, query, onSelect }: Props) {
  if (query.length < 2) return null

  if (isLoading) {
    return (
      <div className="absolute top-full left-0 right-0 mt-1 bg-forest-mid border border-forest-light rounded-xl p-4 flex justify-center z-50">
        <Spinner size="sm" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="absolute top-full left-0 right-0 mt-1 bg-red-900/40 border border-red-500/50 rounded-xl p-4 text-red-300 text-sm z-50">
        API error: {error.message}. Is the backend running on port 8000?
      </div>
    )
  }

  if (!results || results.length === 0) {
    return (
      <div className="absolute top-full left-0 right-0 mt-1 bg-forest-mid border border-forest-light rounded-xl p-4 text-sage text-sm z-50">
        No results for "{query}"
      </div>
    )
  }

  return (
    <div className="absolute top-full left-0 right-0 mt-1 bg-forest-mid border border-forest-light rounded-xl overflow-hidden shadow-xl z-50">
      {results.map(r => (
        <button
          key={r.id}
          onClick={() => onSelect(r)}
          className="w-full text-left px-4 py-3 hover:bg-forest border-b border-forest-light/50 last:border-0 transition-colors"
        >
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-sm text-snow">{r.display_name}</span>
            <EntityBadge type={r.entity_type} />
            {r.organism && <span className="text-xs text-sage">{r.organism}</span>}
          </div>
          {r.matching_synonym && r.matching_synonym !== r.display_name && (
            <div className="text-xs text-sage mt-0.5">matched: {r.matching_synonym}</div>
          )}
          <div className="text-xs text-sage mt-0.5">{r.paper_count} papers</div>
        </button>
      ))}
    </div>
  )
}
