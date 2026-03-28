import { useMutation } from '@tanstack/react-query'
import { findPaths } from '../api/paths'

export function usePaths() {
  return useMutation({
    mutationFn: findPaths,
  })
}
