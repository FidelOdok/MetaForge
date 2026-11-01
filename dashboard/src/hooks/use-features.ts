import { useMutation, useQuery } from '@tanstack/react-query';
import { generateFeature, getFeatureDiff } from '../api/endpoints/features';
import type { GenerateFeaturePayload } from '../types/features';

export function useGenerateFeature() {
  return useMutation({
    mutationFn: (payload: GenerateFeaturePayload) => generateFeature(payload),
  });
}

/** FORGE-270 (gap G-D2): most work products have no prior version. Since
 * FORGE-709 that comes back as a 200 carrying hasPreviousVersion false --
 * the expected, common answer, not a loading/error state. */
export function useFeatureDiff(workProductId: string | undefined) {
  return useQuery({
    queryKey: ['features', 'diff', workProductId ?? ''] as const,
    queryFn: () => getFeatureDiff(workProductId!),
    enabled: !!workProductId,
    staleTime: 15_000,
  });
}
