import { useState, useEffect, useRef } from 'react'
import { useEntitySearch } from '../../hooks/useEntitySearch'
import { usePaths } from '../../hooks/usePaths'
import { PathCard } from './PathCard'
import { Spinner } from '../ui/Spinner'
import { ErrorBanner } from '../ui/ErrorBanner'
import type { PathResult, EntitySearchResult } from '../../types/api'

function useDebounce(value: string, delay: number) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return debounced
}

interface Props {
  sourceEntityName: string
  onPathSelected: (path: PathResult | null) => void
}

export function PathExplorer({ sourceEntityName, onPathSelected }: Props) {
  const [targetQuery, setTargetQuery] = useState('')
  const [targetEntity, setTargetEntity] = useState<EntitySearchResult | null>(null)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [maxHops, setMaxHops] = useState(4)
  const [minConfidence, setMinConfidence] = useState(0.3)
  const [selectedPathIndex, setSelectedPathIndex] = useState<number | null>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)

  const debouncedQuery = useDebounce(targetQuery, 250)
  const { data: searchResults, isLoading: searchLoading } = useEntitySearch(debouncedQuery)
  const { mutate, data, isPending, error, reset } = usePaths()

  // Close dropdown on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  function handleSelectTarget(result: EntitySearchResult) {
    setTargetEntity(result)
    setTargetQuery(result.display_name)
    setDropdownOpen(false)
    reset()
    onPathSelected(null)
    setSelectedPathIndex(null)
  }

  function handleQueryChange(v: string) {
    setTargetQuery(v)
    setTargetEntity(null)
    setDropdownOpen(true)
    reset()
    onPathSelected(null)
    setSelectedPathIndex(null)
  }

  function handleRun() {
    if (!targetEntity) return
    mutate(
      {
        source_entity: sourceEntityName,
        target_entity: targetEntity.display_name,
        max_hops: maxHops,
        min_confidence: minConfidence,
        max_paths: 20,
      },
      {
        onSuccess: () => {
          setSelectedPathIndex(null)
          onPathSelected(null)
        },
      }
    )
  }

  function handlePathClick(path: PathResult, index: number) {
    if (selectedPathIndex === index) {
      setSelectedPathIndex(null)
      onPathSelected(null)
    } else {
      setSelectedPathIndex(index)
      onPathSelected(path)
    }
  }

  return (
    <div className="bg-forest-mid border border-forest-light rounded-xl p-4 space-y-4">
      <div>
        <h3 className="text-xs font-semibold text-sage uppercase tracking-wider">Find Path</h3>
        <p className="text-xs text-sage/70 mt-0.5">Chain search from <span className="text-mist">{sourceEntityName}</span></p>
      </div>

      {/* Target entity input */}
      <div className="relative" ref={dropdownRef}>
        <input
          type="text"
          value={targetQuery}
          onChange={e => handleQueryChange(e.target.value)}
          onFocus={() => setDropdownOpen(true)}
          placeholder="Target entity…"
          className="w-full px-3 py-2 bg-forest border border-forest-light rounded-lg text-sm text-snow placeholder-sage focus:outline-none focus:border-mist focus:ring-1 focus:ring-mist/30"
        />
        {searchLoading && (
          <span className="absolute right-3 top-2.5"><Spinner size="sm" /></span>
        )}
        {dropdownOpen && debouncedQuery.length >= 2 && !searchLoading && searchResults && searchResults.length === 0 && (
          <div className="absolute z-20 top-full mt-1 left-0 right-0 bg-forest-mid border border-forest-light rounded-lg shadow-xl overflow-hidden">
            <p className="px-3 py-2 text-sm text-sage">No entities match "{debouncedQuery}"</p>
          </div>
        )}
        {dropdownOpen && debouncedQuery.length >= 2 && searchResults && searchResults.length > 0 && (
          <div className="absolute z-20 top-full mt-1 left-0 right-0 bg-forest-mid border border-forest-light rounded-lg shadow-xl overflow-hidden max-h-48 overflow-y-auto">
            {searchResults.map(r => (
              <button
                key={r.id}
                onMouseDown={() => handleSelectTarget(r)}
                className="w-full text-left px-3 py-2 hover:bg-forest text-sm text-snow transition-colors"
              >
                <span className="font-medium">{r.display_name}</span>
                <span className="text-sage text-xs ml-2">{r.entity_type}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Controls */}
      <div>
        <label className="text-xs text-sage mb-1 block">
          Max hops: <span className="text-mist font-medium">{maxHops}</span>
        </label>
        <input type="range" min={2} max={5} step={1} value={maxHops}
          onChange={e => { setMaxHops(parseInt(e.target.value)); reset(); onPathSelected(null) }}
          className="w-full" />
      </div>

      <div>
        <label className="text-xs text-sage mb-1 block">
          Min confidence: <span className="text-mist font-medium">{minConfidence.toFixed(1)}</span>
        </label>
        <input type="range" min={0} max={0.9} step={0.1} value={minConfidence}
          onChange={e => { setMinConfidence(parseFloat(e.target.value)); reset(); onPathSelected(null) }}
          className="w-full" />
      </div>

      {/* Run / Clear */}
      <div className="flex gap-2">
        <button
          onClick={handleRun}
          disabled={isPending || !targetEntity}
          className="flex-1 flex items-center justify-center gap-2 py-2 text-sm bg-mist/20 hover:bg-mist/30 border border-mist/50 disabled:opacity-50 text-snow rounded-lg transition-colors"
        >
          {isPending && <Spinner size="sm" />}
          Find Paths
        </button>
        {data && (
          <button
            onClick={() => { reset(); onPathSelected(null); setSelectedPathIndex(null) }}
            className="px-3 py-2 text-sm bg-transparent hover:bg-forest border border-forest-light text-mist rounded-lg transition-colors"
          >
            Clear
          </button>
        )}
      </div>

      {error && <ErrorBanner message={error.message} />}

      {/* Results */}
      {data && (
        <div className="space-y-2">
          <p className="text-xs text-sage font-medium">
            {data.paths.length === 0
              ? 'No paths found'
              : `${data.paths.length} path${data.paths.length !== 1 ? 's' : ''} · ${data.stats.duration_ms}ms`}
          </p>
          {data.paths.map((path, i) => (
            <PathCard
              key={i}
              path={path}
              index={i}
              isSelected={selectedPathIndex === i}
              onClick={() => handlePathClick(path, i)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
