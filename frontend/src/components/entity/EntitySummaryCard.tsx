import { useState } from 'react'
import type { EntityDetail } from '../../types/api'
import { EntityBadge } from './EntityBadge'
import { useEntityConflicts } from '../../hooks/useEntityConflicts'
import { Spinner } from '../ui/Spinner'

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="text-center">
      <div className="text-lg font-bold text-snow">{value}</div>
      <div className="text-xs text-sage">{label}</div>
    </div>
  )
}

export function EntitySummaryCard({ entity }: { entity: EntityDetail }) {
  const [conflictsOpen, setConflictsOpen] = useState(false)
  const { data, isLoading } = useEntityConflicts(entity.id, conflictsOpen)

  return (
    <div className="bg-forest-mid border border-forest-light rounded-xl p-4 space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-lg font-bold text-snow leading-tight">{entity.display_name}</h2>
          {entity.canonical_name !== entity.display_name && (
            <p className="text-xs text-sage mt-0.5">{entity.canonical_name}</p>
          )}
        </div>
        <EntityBadge type={entity.entity_type} />
      </div>

      {entity.organism && (
        <p className="text-xs text-sage italic">{entity.organism}</p>
      )}

      <div className="grid grid-cols-3 gap-2 pt-2 border-t border-forest-light">
        <Stat label="interactions" value={entity.degree_total} />
        <Stat label="evidence"     value={entity.evidence_total} />
        <Stat label="papers"       value={entity.paper_count} />
      </div>

      <div className="text-xs text-sage flex gap-3">
        <span>↑ {entity.degree_in} in</span>
        <span>↓ {entity.degree_out} out</span>
      </div>

      {entity.has_conflicts && (
        <div className="space-y-2">
          <button
            onClick={() => setConflictsOpen(o => !o)}
            className="w-full bg-red-900/40 border border-red-500/50 text-red-300 rounded-lg px-3 py-2 text-xs flex items-center justify-between hover:bg-red-900/60 transition-colors"
          >
            <span>⚠ {entity.open_conflict_count} open conflict{entity.open_conflict_count !== 1 ? 's' : ''}</span>
            <span>{conflictsOpen ? '▲' : '▼'}</span>
          </button>

          {conflictsOpen && (
            <div className="space-y-2">
              {isLoading && <div className="flex justify-center py-2"><Spinner size="sm" /></div>}
              {data?.conflicts.map(c => (
                <div key={c.id} className="bg-red-900/20 border border-red-500/30 rounded-lg px-3 py-2 space-y-1.5 text-xs">
                  <div className="flex flex-wrap gap-1">
                    <span className="px-1.5 py-0.5 rounded bg-red-900/40 border border-red-500/40 text-red-300">
                      {c.conflict_axis}
                    </span>
                    <span className="px-1.5 py-0.5 rounded bg-forest text-sage border border-forest-light">
                      {c.conflict_type.replace(/_/g, ' ')}
                    </span>
                    <span className="px-1.5 py-0.5 rounded bg-forest text-sage border border-forest-light">
                      {c.status}
                    </span>
                  </div>
                  <div className="text-mist space-y-0.5">
                    <div>
                      <span className="text-snow">{c.interaction_a_entity_a}</span>
                      <span className="text-sage"> → </span>
                      <span className="text-snow">{c.interaction_a_entity_b}</span>
                      {c.interaction_a_effect && (
                        <span className="text-sage"> ({c.interaction_a_effect})</span>
                      )}
                    </div>
                    <div className="text-sage text-center text-xs">vs</div>
                    <div>
                      <span className="text-snow">{c.interaction_b_entity_a}</span>
                      <span className="text-sage"> → </span>
                      <span className="text-snow">{c.interaction_b_entity_b}</span>
                      {c.interaction_b_effect && (
                        <span className="text-sage"> ({c.interaction_b_effect})</span>
                      )}
                    </div>
                  </div>
                  {c.llm_analysis && (
                    <p className="text-sage italic border-t border-red-500/20 pt-1.5 leading-relaxed">
                      {c.llm_analysis}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
