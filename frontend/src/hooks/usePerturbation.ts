import { useMutation } from '@tanstack/react-query'
import { runPerturbation } from '../api/perturbation'

export function usePerturbation() {
  return useMutation({
    mutationFn: runPerturbation,
  })
}
