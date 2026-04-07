import { useQuery } from '@tanstack/react-query'
import { getEntityConflicts } from '../api/entities'

export function useEntityConflicts(entityId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ['entity-conflicts', entityId],
    queryFn: () => getEntityConflicts(entityId!),
    enabled: enabled && entityId != null && entityId.length > 0,
    staleTime: 60_000,
  })
}
