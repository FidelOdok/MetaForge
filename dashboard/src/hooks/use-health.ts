import { useQuery } from '@tanstack/react-query';
import { getHealth } from '../api/endpoints/health';

const HEALTH_POLL_MS = 15_000;

export function useHealth() {
  return useQuery({
    queryKey: ['health'] as const,
    queryFn: getHealth,
    staleTime: HEALTH_POLL_MS,
    refetchInterval: HEALTH_POLL_MS,
    retry: 1,
  });
}
