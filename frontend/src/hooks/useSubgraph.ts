import { useQuery } from '@tanstack/react-query'
import { getSubgraph } from '../api/subgraph'
import type { SubgraphRequest } from '../types/api'

export function useSubgraph(req: SubgraphRequest | null) {
  return useQuery({
    queryKey: ['subgraph', req],
    queryFn: () => getSubgraph(req!),
    enabled: req !== null && req.entity_ids.length > 0,
    staleTime: 60_000,
  })
}
