import apiClient from '../client';
import type { GenerateFeaturePayload, GenerateFeatureResult } from '../../types/features';

/** FORGE-269: errors (e.g. an overlapping bolt pattern) propagate to the
 * caller so the form can show them, matching every other create-action
 * endpoint's own precedent in this codebase. */
export async function generateFeature(
  payload: GenerateFeaturePayload,
): Promise<GenerateFeatureResult> {
  const { data } = await apiClient.post<GenerateFeatureResult>('/features/generate', payload);
  return data;
}
