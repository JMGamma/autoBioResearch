import { apiFetch } from './client'
import type { EvidenceResponse } from '../types/api'

export function getEvidence(interactionId: string): Promise<EvidenceResponse> {
  return apiFetch<EvidenceResponse>(`/api/evidence/${encodeURIComponent(interactionId)}`)
}
