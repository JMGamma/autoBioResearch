import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { searchEntities } from '../api/entities'

export function useEntitySearch(q: string, limit = 8) {
  return useQuery({
    queryKey: ['entities', 'search', q, limit],
    queryFn: () => searchEntities({ q, limit }),
    enabled: q.length >= 2,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  })
}
