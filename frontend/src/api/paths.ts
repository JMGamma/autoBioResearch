import { apiFetch } from './client'
import type { PathsRequest, PathsResponse } from '../types/api'

export function findPaths(body: PathsRequest): Promise<PathsResponse> {
  return apiFetch<PathsResponse>('/api/paths', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}
