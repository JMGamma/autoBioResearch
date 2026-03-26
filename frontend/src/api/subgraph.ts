import { apiFetch } from './client'
import type { SubgraphRequest, SubgraphResponse } from '../types/api'

export function getSubgraph(body: SubgraphRequest): Promise<SubgraphResponse> {
  return apiFetch<SubgraphResponse>('/api/subgraph', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}
