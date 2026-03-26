import { useQuery } from '@tanstack/react-query'
import { getEvidence } from '../api/evidence'

export function useEvidence(interactionId: string | null) {
  return useQuery({
    queryKey: ['evidence', interactionId],
    queryFn: () => getEvidence(interactionId!),
    enabled: interactionId !== null,
    staleTime: 120_000,
  })
}
