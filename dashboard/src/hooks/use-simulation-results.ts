import { useQuery } from '@tanstack/react-query';
import { getSimulationResults } from '../api/endpoints/simulationResults';

export const simulationResultKeys = {
  all: ['simulation-results'] as const,
  project: (projectId: string) => [...simulationResultKeys.all, projectId] as const,
};

export function useSimulationResults(projectId?: string) {
  return useQuery({
    queryKey: projectId ? simulationResultKeys.project(projectId) : simulationResultKeys.all,
    queryFn: () => getSimulationResults(projectId),
    staleTime: 30_000,
  });
}
