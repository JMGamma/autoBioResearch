import { useState, useRef, useEffect, useId } from 'react'
import { useEntitySearch } from '../../hooks/useEntitySearch'
import type { EntitySearchResult } from '../../types/api'
import { EntityBadge } from '../entity/EntityBadge'
import { Spinner } from '../ui/Spinner'

function useDebounce(value: string, delay: number) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return debounced
}

interface Props {
  onSelect: (result: EntitySearchResult) => void
  placeholder?: string
  autoFocus?: boolean
  size?: 'default' | 'compact'
}

export function SearchCombobox({ onSelect, placeholder = 'Search genes, proteins, pathways…', autoFocus, size = 'default' }: Props) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const listboxId = useId()
  const debouncedQuery = useDebounce(query, 250)

  const { data: results, isLoading, error } = useEntitySearch(debouncedQuery)

  useEffect(() => {
    if (autoFocus) inputRef.current?.focus()
  }, [autoFocus])

  // Reset active index when results change
  useEffect(() => {
    setActiveIndex(-1)
  }, [debouncedQuery])

  // Close on outside click
  useEffect(() => {
    function onPointerDown(e: PointerEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [])

  const isOpen = open && debouncedQuery.length >= 2
  const resultCount = results?.length ?? 0

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!isOpen) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex(i => Math.min(i + 1, resultCount - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex(i => Math.max(i - 1, -1))
    } else if (e.key === 'Enter') {
      if (activeIndex >= 0 && results?.[activeIndex]) {
        e.preventDefault()
        handleSelect(results[activeIndex])
      }
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setOpen(false)
      setActiveIndex(-1)
    }
  }

  function handleSelect(result: EntitySearchResult) {
    setOpen(false)
    setQuery('')
    setActiveIndex(-1)
    onSelect(result)
  }

  const inputClass = size === 'compact'
    ? 'w-full pl-10 pr-4 py-2 bg-forest border border-forest-light rounded-xl text-snow placeholder-sage text-sm focus:outline-none focus:border-mist focus:ring-1 focus:ring-mist/30'
    : 'w-full pl-12 pr-4 py-3.5 bg-forest-mid border border-forest-light rounded-xl text-snow placeholder-sage text-base focus:outline-none focus:border-mist focus:ring-1 focus:ring-mist/30'

  const iconClass = size === 'compact'
    ? 'absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-sage pointer-events-none'
    : 'absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-sage pointer-events-none'

  return (
    <div ref={containerRef} className="relative">
      <svg className={iconClass} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
        <circle cx="11" cy="11" r="8" />
        <path d="m21 21-4.35-4.35" />
      </svg>
      <input
        ref={inputRef}
        type="text"
        role="combobox"
        aria-expanded={isOpen}
        aria-haspopup="listbox"
        aria-autocomplete="list"
        aria-controls={listboxId}
        aria-activedescendant={activeIndex >= 0 ? `${listboxId}-option-${activeIndex}` : undefined}
        value={query}
        onChange={e => { setQuery(e.target.value); setOpen(true) }}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        className={inputClass}
      />

      {isOpen && (
        <div
          id={listboxId}
          role="listbox"
          className="absolute top-full left-0 right-0 mt-1 bg-forest-mid border border-forest-light rounded-xl overflow-hidden shadow-xl z-50"
        >
          {isLoading && (
            <div className="p-4 flex justify-center">
              <Spinner size="sm" />
            </div>
          )}
          {error && (
            <div className="p-4 text-red-300 text-sm bg-red-900/40 border-b border-red-500/30">
              API error: {error.message}. Is the backend running on port 8000?
            </div>
          )}
          {!isLoading && !error && results && results.length === 0 && (
            <div className="p-4 text-sage text-sm">
              No results for "{debouncedQuery}"
            </div>
          )}
          {!isLoading && !error && results && results.length > 0 && results.map((r, i) => (
            <button
              key={r.id}
              id={`${listboxId}-option-${i}`}
              role="option"
              aria-selected={i === activeIndex}
              onClick={() => handleSelect(r)}
              className={`w-full text-left px-4 py-3 border-b border-forest-light/50 last:border-0 transition-colors ${
                i === activeIndex ? 'bg-forest-light/20' : 'hover:bg-forest'
              }`}
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
      )}
    </div>
  )
}
