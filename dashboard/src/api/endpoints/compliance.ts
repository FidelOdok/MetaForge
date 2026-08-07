import apiClient from '../client';
import type {
  Checklist,
  ComplianceRegime,
  Coverage,
  Evidence,
  LinkEvidencePayload,
} from '../../types/compliance';

/**
 * Generate/fetch the compliance checklist for a project.
 *
 * Backed by ``GET /v1/compliance/{project_id}/checklist``. `markets`
 * defaults server-side to ``UKCA,CE`` when omitted.
 */
export async function getChecklist(
  projectId: string,
  markets?: ComplianceRegime[],
): Promise<Checklist> {
  const params: Record<string, string> = {};
  if (markets && markets.length > 0) params.markets = markets.join(',');
  const { data } = await apiClient.get<Checklist>(`/compliance/${projectId}/checklist`, { params });
  return data;
}

/** Backed by ``GET /v1/compliance/{project_id}/coverage``. */
export async function getCoverage(projectId: string): Promise<Coverage> {
  const { data } = await apiClient.get<Coverage>(`/compliance/${projectId}/coverage`);
  return data;
}

/** Link a piece of evidence to a checklist item — ``POST /v1/compliance/{project_id}/evidence``. */
export async function linkEvidence(
  projectId: string,
  payload: LinkEvidencePayload,
): Promise<Evidence> {
  const { data } = await apiClient.post<Evidence>(`/compliance/${projectId}/evidence`, payload);
  return data;
}

/** All evidence records for a checklist item — ``GET /v1/compliance/{project_id}/evidence/{item_id}``. */
export async function getEvidenceForItem(projectId: string, itemId: string): Promise<Evidence[]> {
  const { data } = await apiClient.get<Evidence[]>(`/compliance/${projectId}/evidence/${itemId}`);
  return data;
}
