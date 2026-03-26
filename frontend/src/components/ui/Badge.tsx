interface BadgeProps {
  label: string
  color?: 'blue' | 'sky' | 'green' | 'amber' | 'red' | 'purple' | 'muted' | 'orange'
  size?: 'sm' | 'xs'
}

const COLOR_CLASSES: Record<string, string> = {
  blue:   'bg-blue-900/50   text-blue-200   border-blue-600/50',
  sky:    'bg-sky-900/50    text-sky-200    border-sky-600/50',
  green:  'bg-green-900/50  text-green-200  border-green-600/50',
  amber:  'bg-amber-900/50  text-amber-200  border-amber-600/50',
  red:    'bg-red-900/50    text-red-200    border-red-600/50',
  purple: 'bg-purple-900/50 text-purple-200 border-purple-600/50',
  muted:  'bg-forest/60     text-mist       border-forest-light',
  orange: 'bg-orange-900/50 text-orange-200 border-orange-600/50',
}

export function Badge({ label, color = 'muted', size = 'sm' }: BadgeProps) {
  const sizeClass = size === 'xs' ? 'text-xs px-1.5 py-0.5' : 'text-xs px-2 py-1'
  return (
    <span className={`inline-flex items-center rounded border font-medium ${sizeClass} ${COLOR_CLASSES[color]}`}>
      {label}
    </span>
  )
}
