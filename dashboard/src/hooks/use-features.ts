import { useMutation, useQuery } from '@tanstack/react-query';
import { generateFeature, getFeatureDiff } from '../api/endpoints/features';
import type { GenerateFeaturePayload } from '../types/features';

export function useGenerateFeature() {
  return useMutation({
    mutationFn: (payload: GenerateFeaturePayload) => generateFeature(payload),
  });
}

/** FORGE-270 (gap G-D2): null for most work products (no prior version) --
 * that's the expected, common answer, not a loading/error state. */
export function useFeatureDiff(workProductId: string | undefined) {
  return useQuery({
    queryKey: ['features', 'diff', workProductId ?? ''] as const,
    queryFn: () => getFeatureDiff(workProductId!),
    enabled: !!workProductId,
    staleTime: 15_000,
  });
}
