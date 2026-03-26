import { useQuery } from '@tanstack/react-query'
import { getStats } from '../api/stats'

export function useStats() {
  return useQuery({
    queryKey: ['stats'],
    queryFn: getStats,
    staleTime: 300_000,
  })
}
