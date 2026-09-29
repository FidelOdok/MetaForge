import { useMutation } from '@tanstack/react-query';
import { generateFeature } from '../api/endpoints/features';
import type { GenerateFeaturePayload } from '../types/features';

export function useGenerateFeature() {
  return useMutation({
    mutationFn: (payload: GenerateFeaturePayload) => generateFeature(payload),
  });
}
