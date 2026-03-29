import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { SearchBar } from '../components/search/SearchBar'
import { SearchResults } from '../components/search/SearchResults'
import { StatsBar } from '../components/stats/StatsBar'
import { useEntitySearch } from '../hooks/useEntitySearch'
import { useStats } from '../hooks/useStats'
import type { EntitySearchResult } from '../types/api'

function useDebounce(value: string, delay: number) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return debounced
}

export function HomePage() {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const debouncedQuery = useDebounce(query, 250)

  const { data: results, isLoading, error: searchError } = useEntitySearch(debouncedQuery)
  const { data: stats, error: statsError } = useStats()

  function handleSelect(r: EntitySearchResult) {
    setOpen(false)
    setQuery('')
    navigate(`/entity/${r.id}`)
  }

  useEffect(() => {
    function onPointerDown(e: PointerEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [])

  return (
    <div className="min-h-screen bg-forest-dark flex flex-col items-center justify-center px-4 py-16">
      <div className="w-full max-w-2xl space-y-8">
        {/* Header */}
        <div className="text-center space-y-2">
          <h1 className="text-4xl font-bold text-snow tracking-tight">
            AutoBio<span className="text-sage">Research</span>
          </h1>
          <p className="text-mist text-lg">
            Explore biological interaction networks from the literature
          </p>
        </div>

        {/* Search */}
        <div ref={containerRef} className="relative">
          <SearchBar
            value={query}
            onChange={v => { setQuery(v); setOpen(true) }}
            autoFocus
          />
          {open && (
            <SearchResults
              results={results}
              isLoading={isLoading}
              error={searchError}
              query={debouncedQuery}
              onSelect={handleSelect}
            />
          )}
        </div>

        {/* Stats */}
        <StatsBar stats={stats} error={statsError} />

        <p className="text-center text-xs text-sage">
          Search for genes, proteins, pathways, or diseases to explore their interaction neighborhood
        </p>
      </div>
    </div>
  )
}
