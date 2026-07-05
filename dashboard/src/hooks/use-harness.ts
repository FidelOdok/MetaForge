import { useQuery } from '@tanstack/react-query';
import {
  getHarnessProviders,
  getHarnessModels,
  getHarnessTools,
} from '@/api/endpoints/harness';

// React Query hooks for the chat model + tools/connectors selector (MET-548).

export function useHarnessProviders() {
  return useQuery({
    queryKey: ['harness', 'providers'],
    queryFn: getHarnessProviders,
    staleTime: 60_000,
  });
}

export function useHarnessModels(provider: string | null) {
  return useQuery({
    queryKey: ['harness', 'models', provider],
    queryFn: () => getHarnessModels(provider as string),
    enabled: !!provider,
    staleTime: 300_000,
  });
}

export function useHarnessTools() {
  return useQuery({
    queryKey: ['harness', 'tools'],
    queryFn: getHarnessTools,
    staleTime: 60_000,
  });
}
