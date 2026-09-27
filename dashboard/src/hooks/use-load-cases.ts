import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createLoadCase, getLoadCases } from '../api/endpoints/loadCases';

export const loadCaseKeys = {
  all: ['load-cases'] as const,
  project: (projectId: string) => [...loadCaseKeys.all, projectId] as const,
};

export function useLoadCases(projectId?: string) {
  return useQuery({
    queryKey: projectId ? loadCaseKeys.project(projectId) : loadCaseKeys.all,
    queryFn: () => getLoadCases(projectId),
    staleTime: 30_000,
  });
}

export function useCreateLoadCase(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createLoadCase,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: loadCaseKeys.project(projectId) });
    },
  });
}
