import { useQuery } from '@tanstack/react-query'
import { getEntity } from '../api/entities'

export function useEntityDetail(id: string | undefined) {
  return useQuery({
    queryKey: ['entity', id],
    queryFn: () => getEntity(id!),
    enabled: id != null && id.length > 0,
    staleTime: 60_000,
  })
}
