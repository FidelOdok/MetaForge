import apiClient from '../client';
import { isSampleMode } from '../../lib/sample-workspace';

/** Where the AI budget paying for a project's model calls stands (FORGE-873). */
export interface BudgetStatus {
  /** False on a gateway with no budget (a laptop): nothing to show. */
  enabled: boolean;
  /** False when the gateway could not say; not the same as nothing spent. */
  available?: boolean;
  period?: string;
  monthly_usd?: number;
  spent_usd?: number;
  remaining_usd?: number;
  default_budget?: boolean;
  exhausted?: boolean;
}

export async function getBudget(projectId: string): Promise<BudgetStatus> {
  if (isSampleMode()) return { enabled: false };
  const { data } = await apiClient.get<BudgetStatus>('/runs/usage/budget', {
    params: { project_id: projectId },
  });
  return data;
}
