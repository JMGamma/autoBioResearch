import { apiFetch } from './client'
import type { EntitySearchResult, EntityDetail } from '../types/api'

export function searchEntities(params: {
  q: string
  entity_type?: string
  organism?: string
  limit?: number
}): Promise<EntitySearchResult[]> {
  const qs = new URLSearchParams()
  qs.set('q', params.q)
  if (params.entity_type) qs.set('entity_type', params.entity_type)
  if (params.organism) qs.set('organism', params.organism)
  if (params.limit != null) qs.set('limit', String(params.limit))
  return apiFetch<EntitySearchResult[]>(`/api/entities/search?${qs}`)
}

export function getEntity(id: string): Promise<EntityDetail> {
  return apiFetch<EntityDetail>(`/api/entities/${encodeURIComponent(id)}`)
}
