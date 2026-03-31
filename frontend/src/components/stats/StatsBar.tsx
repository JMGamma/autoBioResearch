function Stat({ label, value, tooltip }: { label: string; value: string | number | undefined; tooltip?: string }) {
  return (
    <div className="text-center">
      <div className="text-2xl font-bold text-snow">
        {value != null ? Number(value).toLocaleString() : '—'}
      </div>
      <div className="text-xs text-sage mt-0.5 uppercase tracking-wide flex items-center justify-center gap-1">
        {label}
        {tooltip && (
          <span
            title={tooltip}
            className="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full border border-sage/50 text-sage/70 text-[9px] cursor-help leading-none"
          >
            ?
          </span>
        )}
      </div>
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
    <div className="grid grid-cols-5 gap-4 py-5 px-8 bg-forest-mid border border-forest-light rounded-xl">
      <Stat label="entities"     value={stats?.n_entities} />
      <Stat label="interactions" value={stats?.n_interactions} />
      <Stat label="evidence"     value={stats?.n_evidence} />
      <Stat label="papers"       value={stats?.n_papers} />
      <Stat
        label="literature conflicts"
        value={stats?.n_open_conflicts}
        tooltip="Interactions where papers disagree on direction or effect — explore to investigate."
      />
    </div>
  )
}
