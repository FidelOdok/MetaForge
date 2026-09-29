import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getRequirementMatrix,
  getRequirementQuality,
  proposeRequirementFix,
} from '../api/endpoints/requirements';

export const requirementKeys = {
  all: ['requirements', 'quality'] as const,
  project: (projectId: string, productType: string) =>
    [...requirementKeys.all, projectId, productType] as const,
  matrixAll: ['requirements', 'matrix'] as const,
  matrixProject: (projectId: string) => [...requirementKeys.matrixAll, projectId] as const,
};

export function useRequirementQuality(projectId?: string, productType = 'generic') {
  return useQuery({
    queryKey: projectId ? requirementKeys.project(projectId, productType) : requirementKeys.all,
    queryFn: () => getRequirementQuality(projectId, productType),
    staleTime: 30_000,
  });
}

/** FORGE-318: requirements x claims x evidence, status derived live. */
export function useRequirementMatrix(projectId?: string) {
  return useQuery({
    queryKey: projectId ? requirementKeys.matrixProject(projectId) : requirementKeys.matrixAll,
    queryFn: () => getRequirementMatrix(projectId),
    staleTime: 30_000,
  });
}

/** FORGE-257: "fix with AI" -- proposes a rewrite, never applies one. */
export function useProposeRequirementFix() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (requirementId: string) => proposeRequirementFix(requirementId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: requirementKeys.all });
    },
  });
}
