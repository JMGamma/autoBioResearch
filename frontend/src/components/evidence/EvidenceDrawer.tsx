import { useEffect, useRef, useId } from 'react'
import type { EvidenceItem } from '../../types/api'
import { useEvidence } from '../../hooks/useEvidence'
import { Spinner } from '../ui/Spinner'
import { ErrorBanner } from '../ui/ErrorBanner'

const CONFIDENCE_COLORS: Record<string, string> = {
  high:   'bg-green-900/40 text-green-300 border-green-600/40',
  medium: 'bg-amber-900/40 text-amber-300 border-amber-600/40',
  low:    'bg-red-900/40   text-red-300   border-red-600/40',
}

function EvidenceCard({ item }: { item: EvidenceItem }) {
  const confColor = CONFIDENCE_COLORS[item.confidence?.toLowerCase()] ?? 'bg-forest text-mist border-forest-light'
  const contexts = [item.organism, item.tissue_cell_type, item.condition].filter(Boolean)

  return (
    <div className="border border-forest-light rounded-lg p-3 space-y-2 bg-forest-dark/40">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs px-2 py-0.5 rounded border bg-forest text-mist border-forest-light">
          {item.evidence_type}
        </span>
        <span className={`text-xs px-2 py-0.5 rounded border ${confColor}`}>
          {item.confidence} ({item.confidence_score.toFixed(2)})
        </span>
      </div>

      {item.paper_title && (
        <div>
          <p className="text-sm font-medium text-snow leading-snug">{item.paper_title}</p>
          <p className="text-xs text-sage mt-0.5">
            {[item.paper_year, item.paper_journal].filter(Boolean).join(' · ')}
          </p>
          {item.paper_doi && (
            <a href={`https://doi.org/${item.paper_doi}`} target="_blank" rel="noopener noreferrer"
              className="text-xs text-mist underline hover:text-snow">
              {item.paper_doi}
            </a>
          )}
        </div>
      )}

      {item.snippet && (
        <p className="text-xs text-mist italic border-l-2 border-forest-light pl-2">
          "{item.snippet}"
        </p>
      )}

      {contexts.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {contexts.map((c, i) => (
            <span key={i} className="text-xs px-1.5 py-0.5 bg-forest text-sage rounded border border-forest-light">
              {c}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

export function EvidenceDrawer({ interactionId, onClose }: { interactionId: string | null; onClose: () => void }) {
  const { data, isLoading, error } = useEvidence(interactionId)
  const isOpen = interactionId !== null
  const closeBtnRef = useRef<HTMLButtonElement>(null)
  const titleId = useId()

  // Focus close button when drawer opens
  useEffect(() => {
    if (isOpen) {
      // Small delay to allow CSS transition to start
      const t = setTimeout(() => closeBtnRef.current?.focus(), 50)
      return () => clearTimeout(t)
    }
  }, [isOpen])

  // Close on Escape
  useEffect(() => {
    if (!isOpen) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [isOpen, onClose])

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      className={`fixed top-0 right-0 h-full w-96 max-w-full bg-forest-mid border-l border-forest-light shadow-2xl z-50 flex flex-col transition-transform duration-300 ease-in-out ${isOpen ? 'translate-x-0' : 'translate-x-full'}`}
    >
      <div className="flex items-center justify-between px-4 py-3 border-b border-forest-light bg-forest flex-shrink-0">
        <div>
          <h3 id={titleId} className="text-sm font-semibold text-snow">Evidence</h3>
          {data?.entity_a_name && (
            <p className="text-xs text-sage mt-0.5">
              {data.entity_a_name} → {data.entity_b_name}
            </p>
          )}
        </div>
        <button
          ref={closeBtnRef}
          onClick={onClose}
          className="text-sage hover:text-snow p-1 rounded transition-colors"
          aria-label="Close evidence drawer"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path d="M6 18 18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {isLoading && <div className="flex justify-center py-8"><Spinner /></div>}
        {error && <ErrorBanner message={error.message} />}
        {data && data.evidence.length === 0 && (
          <p className="text-sm text-sage text-center py-8">No evidence records found.</p>
        )}
        {data?.evidence.map(item => <EvidenceCard key={item.id} item={item} />)}
      </div>

      {data && (
        <div className="px-4 py-2 border-t border-forest-light text-xs text-sage bg-forest flex-shrink-0">
          {data.evidence.length} evidence record{data.evidence.length !== 1 ? 's' : ''}
        </div>
      )}
    </div>
  )
}
