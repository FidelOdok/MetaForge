import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createBringupChecklist, listBringupChecklists } from '../api/endpoints/bringup';

export const bringupKeys = {
  all: ['bringup'] as const,
  workProduct: (workProductId: string) => [...bringupKeys.all, workProductId] as const,
};

/** FORGE-295: a work product's generated bring-up checklists, oldest
 * first (scoped by work_product_id, not project_id -- checklists aren't
 * project-scoped the way test plans/release packages are). */
export function useBringupChecklists(workProductId?: string) {
  return useQuery({
    queryKey: workProductId ? bringupKeys.workProduct(workProductId) : bringupKeys.all,
    queryFn: () => listBringupChecklists(workProductId as string),
    enabled: !!workProductId,
    staleTime: 15_000,
  });
}

/** FORGE-295: derive and record a bring-up checklist from the work
 * product's real committed assembly.joints -- a pure append (calling
 * twice creates a second checklist entity, mirroring release-package's
 * own create-a-new-snapshot-each-call semantics). */
export function useCreateBringupChecklist(workProductId?: string, projectId?: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => {
      if (!workProductId) throw new Error('useCreateBringupChecklist: no work product');
      return createBringupChecklist({ workProductId, projectId });
    },
    onSuccess: () => {
      if (workProductId) {
        queryClient.invalidateQueries({ queryKey: bringupKeys.workProduct(workProductId) });
      }
    },
  });
}
