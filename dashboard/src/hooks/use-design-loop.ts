import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  approveDesignLoop,
  getDesignLoop,
  startDesignLoop,
} from '../api/endpoints/design-loop';
import type { StartDesignLoopPayload } from '../types/design-loop';

export const designLoopKeys = {
  loop: (loopId: string) => ['design-loop', loopId] as const,
};

/** FORGE-287: the iteration timeline for one started loop. */
export function useDesignLoop(loopId?: string) {
  return useQuery({
    queryKey: loopId ? designLoopKeys.loop(loopId) : ['design-loop', 'none'],
    queryFn: () => getDesignLoop(loopId),
    enabled: !!loopId,
    staleTime: 10_000,
  });
}

export function useStartDesignLoop() {
  return useMutation({
    mutationFn: (payload: StartDesignLoopPayload) => startDesignLoop(payload),
  });
}

export function useApproveDesignLoop() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ loopId, approvedBy }: { loopId: string; approvedBy: string }) =>
      approveDesignLoop(loopId, approvedBy),
    onSuccess: (_result, { loopId }) => {
      queryClient.invalidateQueries({ queryKey: designLoopKeys.loop(loopId) });
    },
  });
}
