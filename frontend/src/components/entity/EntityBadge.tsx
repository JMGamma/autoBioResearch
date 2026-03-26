import { Badge } from '../ui/Badge'

const TYPE_COLORS: Record<string, 'blue' | 'sky' | 'green' | 'amber' | 'red' | 'purple' | 'muted' | 'orange'> = {
  gene:      'blue',
  protein:   'sky',
  molecule:  'green',
  pathway:   'amber',
  disease:   'red',
  cell_type: 'purple',
  organism:  'muted',
}

export function EntityBadge({ type }: { type: string }) {
  const color = TYPE_COLORS[type.toLowerCase()] ?? 'muted'
  return <Badge label={type} color={color} />
}
