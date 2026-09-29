import apiClient from '../client';
import type {
  FeatureDiff,
  GenerateFeaturePayload,
  GenerateFeatureResult,
} from '../../types/features';

/** FORGE-269: errors (e.g. an overlapping bolt pattern) propagate to the
 * caller so the form can show them, matching every other create-action
 * endpoint's own precedent in this codebase. */
export async function generateFeature(
  payload: GenerateFeaturePayload,
): Promise<GenerateFeatureResult> {
  const { data } = await apiClient.post<GenerateFeatureResult>('/features/generate', payload);
  return data;
}

/** FORGE-270: a 404 (no prior version -- most work products) is an
 * expected, common answer, not an error -- returns null rather than
 * propagating, unlike generateFeature's own create-action errors above. */
export async function getFeatureDiff(workProductId: string): Promise<FeatureDiff | null> {
  try {
    const { data } = await apiClient.get<FeatureDiff>(`/features/${workProductId}/diff`);
    return data;
  } catch {
    return null;
  }
}
