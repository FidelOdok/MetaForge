import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { attemptPromotion, getPromotionHistory } from '../api/endpoints/promotion';
import type { AttemptPromotionPayload } from '../types/promotion';

export const promotionKeys = {
  history: (projectId: string) => ['promotion', 'history', projectId] as const,
};

export function usePromotionHistory(projectId?: string) {
  return useQuery({
    queryKey: projectId ? promotionKeys.history(projectId) : ['promotion', 'history', 'none'],
    queryFn: () => getPromotionHistory(projectId),
    enabled: !!projectId,
    staleTime: 10_000,
  });
}

export function useAttemptPromotion() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: AttemptPromotionPayload) => attemptPromotion(payload),
    onSuccess: (_result, payload) => {
      queryClient.invalidateQueries({ queryKey: promotionKeys.history(payload.projectId) });
    },
  });
}
