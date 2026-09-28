import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getRequirementQuality, proposeRequirementFix } from '../api/endpoints/requirements';

export const requirementKeys = {
  all: ['requirements', 'quality'] as const,
  project: (projectId: string, productType: string) =>
    [...requirementKeys.all, projectId, productType] as const,
};

export function useRequirementQuality(projectId?: string, productType = 'generic') {
  return useQuery({
    queryKey: projectId ? requirementKeys.project(projectId, productType) : requirementKeys.all,
    queryFn: () => getRequirementQuality(projectId, productType),
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
