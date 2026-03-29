function Stat({ label, value }: { label: string; value: string | number | undefined }) {
  return (
    <div className="text-center">
      <div className="text-2xl font-bold text-snow">
        {value != null ? Number(value).toLocaleString() : '—'}
      </div>
      <div className="text-xs text-sage mt-0.5 uppercase tracking-wide">{label}</div>
    </div>
  )
}

export function StatsBar({ stats, error }: { stats?: import('../../types/api').StatsResponse; error?: Error | null }) {
  if (error) {
    return (
      <div className="py-3 px-8 bg-forest-mid border border-forest-light rounded-xl text-center text-xs text-sage/60">
        Stats unavailable
      </div>
    )
  }
  return (
    <div className="grid grid-cols-4 gap-6 py-5 px-8 bg-forest-mid border border-forest-light rounded-xl">
      <Stat label="entities"       value={stats?.n_entities} />
      <Stat label="interactions"   value={stats?.n_interactions} />
      <Stat label="evidence"       value={stats?.n_evidence} />
      <Stat label="open conflicts" value={stats?.n_open_conflicts} />
    </div>
  )
}
