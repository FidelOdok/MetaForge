import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { generateTestPlan, listTestPlan } from '../api/endpoints/testplans';

export const testPlanKeys = {
  all: ['testplans'] as const,
  project: (projectId: string) => [...testPlanKeys.all, projectId] as const,
};

/** FORGE-298: a project's generated test-plan entries, oldest first. */
export function useTestPlan(projectId?: string) {
  return useQuery({
    queryKey: projectId ? testPlanKeys.project(projectId) : testPlanKeys.all,
    queryFn: () => listTestPlan(projectId),
    staleTime: 15_000,
  });
}

/** FORGE-298: generate a test plan from the project's real test-method
 * requirements -- a pure append (calling twice creates duplicate entries
 * per requirement, mirroring release-package's own create-a-new-snapshot
 * semantics). */
export function useGenerateTestPlan(projectId?: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => {
      if (!projectId) throw new Error('useGenerateTestPlan: no active project');
      return generateTestPlan(projectId);
    },
    onSuccess: () => {
      if (projectId) {
        queryClient.invalidateQueries({ queryKey: testPlanKeys.project(projectId) });
      }
    },
  });
}
