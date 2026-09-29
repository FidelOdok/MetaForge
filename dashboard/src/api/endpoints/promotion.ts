import apiClient from '../client';
import type {
  AttemptPromotionPayload,
  AttemptPromotionResult,
  PromotionHistoryReport,
} from '../../types/promotion';

const EMPTY_HISTORY: PromotionHistoryReport = { gates: [] };

/** FORGE-290: evidence-gated approval attempt -- errors (e.g. an unknown
 * requirement id) propagate to the caller, unlike the read endpoint below,
 * since a failed attempt needs to reach the form's error state. */
export async function attemptPromotion(
  payload: AttemptPromotionPayload,
): Promise<AttemptPromotionResult> {
  const { data } = await apiClient.post<AttemptPromotionResult>('/promotion/attempt', payload);
  return data;
}

export async function getPromotionHistory(projectId?: string): Promise<PromotionHistoryReport> {
  if (!projectId) return EMPTY_HISTORY;
  try {
    const { data } = await apiClient.get<PromotionHistoryReport>('/promotion', {
      params: { project_id: projectId },
    });
    return data;
  } catch {
    return EMPTY_HISTORY;
  }
}
