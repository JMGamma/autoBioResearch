import { apiFetch } from './client'
import type { StatsResponse } from '../types/api'

export function getStats(): Promise<StatsResponse> {
  return apiFetch<StatsResponse>('/api/stats')
}
