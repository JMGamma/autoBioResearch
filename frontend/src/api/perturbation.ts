import { apiFetch } from './client'
import type { PerturbationRequest, PerturbationResponse } from '../types/api'

export function runPerturbation(body: PerturbationRequest): Promise<PerturbationResponse> {
  return apiFetch<PerturbationResponse>('/api/perturbation', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}
