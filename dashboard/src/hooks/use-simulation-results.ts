import { useQuery } from '@tanstack/react-query';
import { getSimulationField, getSimulationResults } from '../api/endpoints/simulationResults';

export const simulationResultKeys = {
  all: ['simulation-results'] as const,
  project: (projectId: string) => [...simulationResultKeys.all, projectId] as const,
  field: (resultId: string) => [...simulationResultKeys.all, 'field', resultId] as const,
};

export function useSimulationResults(projectId?: string) {
  return useQuery({
    queryKey: projectId ? simulationResultKeys.project(projectId) : simulationResultKeys.all,
    queryFn: () => getSimulationResults(projectId),
    staleTime: 30_000,
  });
}

/** FORGE-532: one result's 3D field. A stored field never changes (a new
 * run is a new result), so it is cached for the session. */
export function useSimulationField(resultId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: simulationResultKeys.field(resultId ?? ''),
    queryFn: () => getSimulationField(resultId as string),
    enabled: Boolean(resultId) && enabled,
    staleTime: Infinity,
    gcTime: 10 * 60_000,
    retry: false,
  });
}
