import type { EntityDetail } from '../../types/api'
import { EntityBadge } from './EntityBadge'

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="text-center">
      <div className="text-lg font-bold text-snow">{value}</div>
      <div className="text-xs text-sage">{label}</div>
    </div>
  )
}

export function EntitySummaryCard({ entity }: { entity: EntityDetail }) {
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
        <div className="bg-red-900/40 border border-red-500/50 text-red-300 rounded-lg px-3 py-2 text-xs">
          ⚠ {entity.open_conflict_count} open conflict{entity.open_conflict_count !== 1 ? 's' : ''}
        </div>
      )}
    </div>
  )
}
