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

/** FORGE-270: no prior version is the common answer for most work products.
 * FORGE-709: the gateway says so with a 200 carrying hasPreviousVersion
 * false, so this no longer swallows a 404 -- a real failure propagates like
 * generateFeature's own errors above, and the ordinary answer no longer
 * reaches the error log. */
export async function getFeatureDiff(workProductId: string): Promise<FeatureDiff> {
  const { data } = await apiClient.get<FeatureDiff>(`/features/${workProductId}/diff`);
  return data;
}
