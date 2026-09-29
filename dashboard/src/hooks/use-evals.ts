import { useQuery } from '@tanstack/react-query';
import { getEvals } from '../api/endpoints/evals';

export function useEvals() {
  return useQuery({
    queryKey: ['evals'],
    queryFn: getEvals,
    staleTime: 30_000,
  });
}
