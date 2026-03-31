import { useNavigate } from 'react-router-dom'
import { SearchCombobox } from '../components/search/SearchCombobox'
import { StatsBar } from '../components/stats/StatsBar'
import { useStats } from '../hooks/useStats'
import type { EntitySearchResult } from '../types/api'

export function HomePage() {
  const navigate = useNavigate()
  const { data: stats, error: statsError } = useStats()

  function handleSelect(r: EntitySearchResult) {
    navigate(`/entity/${r.id}`)
  }

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
        <SearchCombobox onSelect={handleSelect} autoFocus />

        {/* Stats */}
        <StatsBar stats={stats} error={statsError} />

        <p className="text-center text-xs text-sage">
          Search for genes, proteins, pathways, or diseases to explore their interaction neighborhood
        </p>
      </div>
    </div>
  )
}
