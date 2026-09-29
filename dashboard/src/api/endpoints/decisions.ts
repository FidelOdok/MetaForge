import apiClient from '../client';
import type { Decision } from '../../types/decision';

/** FORGE-289: "what decisions touch this node" -- a hierarchy node or a
 * DesignLoopIteration id. Read endpoint: degrades to an empty list rather
 * than throwing, matching getDesignLoop/getHierarchyTree's own convention
 * for a panel that should just render "no decisions yet" on any failure. */
export async function getRelatedDecisions(nodeId?: string): Promise<Decision[]> {
  if (!nodeId) return [];
  try {
    const { data } = await apiClient.get<{ decisions: Decision[] }>('/decisions', {
      params: { related_to: nodeId },
    });
    return data.decisions;
  } catch {
    return [];
  }
}
