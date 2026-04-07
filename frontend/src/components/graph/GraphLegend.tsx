import { ENTITY_TYPE_COLORS, EFFECT_EDGE_COLORS } from './cytoscapeConfig'

export function GraphLegend({ entityTypes, showPerturbation }: { entityTypes: string[]; showPerturbation: boolean }) {
  const effects = [
    { key: 'activates', label: 'Activates' },
    { key: 'inhibits',  label: 'Inhibits' },
    { key: 'modifies',  label: 'Modifies' },
    { key: 'regulates', label: 'Regulates' },
    { key: 'ambiguous', label: 'Ambiguous' },
    { key: 'unknown',   label: 'Unknown' },
  ]

  return (
    <div className="absolute bottom-3 left-3 bg-forest-dark/90 border border-forest-light rounded-lg p-3 text-xs space-y-2 max-w-[180px] backdrop-blur-sm">
      {entityTypes.length > 0 && (
        <div>
          <div className="text-sage mb-1 font-semibold uppercase tracking-wide text-[10px]">Entity type</div>
          {entityTypes.map(t => (
            <div key={t} className="flex items-center gap-1.5 mb-0.5">
              <span className="w-3 h-3 rounded-full flex-shrink-0"
                style={{ background: ENTITY_TYPE_COLORS[t.toLowerCase()] ?? ENTITY_TYPE_COLORS.default }} />
              <span className="text-mist">{t}</span>
            </div>
          ))}
        </div>
      )}
      <div>
        <div className="text-sage mb-1 font-semibold uppercase tracking-wide text-[10px]">Edge effect</div>
        {effects.map(({ key, label }) => (
          <div key={key} className="flex items-center gap-1.5 mb-0.5">
            <span className="w-4 h-0.5 flex-shrink-0" style={{ background: EFFECT_EDGE_COLORS[key] }} />
            <span className="text-mist">{label}</span>
          </div>
        ))}
      </div>
      {showPerturbation && (
        <div>
          <div className="text-sage mb-1 font-semibold uppercase tracking-wide text-[10px]">Perturbation</div>
          <div className="flex items-center gap-1.5 mb-0.5">
            <span className="text-green-400 font-bold text-sm">+</span>
            <span className="text-mist">Upregulated</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-red-400 font-bold text-sm">−</span>
            <span className="text-mist">Downregulated</span>
          </div>
        </div>
      )}
    </div>
  )
}
