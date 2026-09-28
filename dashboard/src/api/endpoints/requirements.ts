import apiClient from '../client';
import type { RequirementFixProposal, RequirementSetQualityReport } from '../../types/requirements';

const EMPTY_REPORT: RequirementSetQualityReport = {
  requirements: [],
  conflicts: [],
  completeness: { productType: 'generic', covered: [], missing: [] },
};

export async function getRequirementQuality(
  projectId?: string,
  productType = 'generic',
): Promise<RequirementSetQualityReport> {
  if (!projectId) return EMPTY_REPORT;
  try {
    const { data } = await apiClient.get<RequirementSetQualityReport>('/requirements/quality', {
      params: { project_id: projectId, product_type: productType },
    });
    return data;
  } catch {
    return EMPTY_REPORT;
  }
}

export async function proposeRequirementFix(requirementId: string): Promise<RequirementFixProposal> {
  const { data } = await apiClient.post<RequirementFixProposal>(
    `/requirements/${requirementId}/fix`,
  );
  return data;
}
