import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  approveTechnicalDrawing,
  listTechnicalDrawings,
} from '../api/endpoints/technicalDrawing';

export const technicalDrawingKeys = {
  all: ['technicalDrawings'] as const,
  workProduct: (workProductId: string) => [...technicalDrawingKeys.all, workProductId] as const,
};

/** FORGE-293: a part's real recorded technical_drawing work products
 * (scoped by work_product_id, same pattern as bring-up checklists). */
export function useTechnicalDrawings(workProductId?: string) {
  return useQuery({
    queryKey: workProductId
      ? technicalDrawingKeys.workProduct(workProductId)
      : technicalDrawingKeys.all,
    queryFn: () => listTechnicalDrawings(workProductId as string),
    enabled: !!workProductId,
    staleTime: 15_000,
  });
}

/** FORGE-293: human sign-off on a drawing -- invalidates the list so the
 * approved state refreshes without a manual refetch. */
export function useApproveTechnicalDrawing(workProductId?: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ nodeId, approvedBy }: { nodeId: string; approvedBy?: string }) =>
      approveTechnicalDrawing(nodeId, approvedBy),
    onSuccess: () => {
      if (workProductId) {
        queryClient.invalidateQueries({
          queryKey: technicalDrawingKeys.workProduct(workProductId),
        });
      }
    },
  });
}
