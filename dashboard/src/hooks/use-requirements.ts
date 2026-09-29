import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  createConstraint,
  getRequirementMatrix,
  getRequirementQuality,
  proposeRequirementFix,
} from '../api/endpoints/requirements';
import type { CreateConstraintPayload } from '../types/requirements';

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

/** FORGE-259: the constraint editor's create action -- refetches both the
 * quality report and the live evidence matrix so the new requirement
 * shows up immediately (as `no_data`, honestly, until a claim cites it). */
export function useCreateConstraint() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateConstraintPayload) => createConstraint(payload),
    onSuccess: (_result, payload) => {
      queryClient.invalidateQueries({ queryKey: requirementKeys.all });
      queryClient.invalidateQueries({
        queryKey: requirementKeys.matrixProject(payload.projectId),
      });
    },
  });
}
