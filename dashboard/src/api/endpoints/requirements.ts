import apiClient from '../client';
import type {
  CreateConstraintPayload,
  CreateConstraintResult,
  RequirementFixProposal,
  RequirementMatrixReport,
  RequirementSetQualityReport,
} from '../../types/requirements';

const EMPTY_REPORT: RequirementSetQualityReport = {
  requirements: [],
  conflicts: [],
  completeness: { productType: 'generic', covered: [], missing: [] },
};

const EMPTY_MATRIX: RequirementMatrixReport = { rows: [] };

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

export async function getRequirementMatrix(projectId?: string): Promise<RequirementMatrixReport> {
  if (!projectId) return EMPTY_MATRIX;
  try {
    const { data } = await apiClient.get<RequirementMatrixReport>('/requirements/matrix', {
      params: { project_id: projectId },
    });
    return data;
  } catch {
    return EMPTY_MATRIX;
  }
}

export async function proposeRequirementFix(requirementId: string): Promise<RequirementFixProposal> {
  const { data } = await apiClient.post<RequirementFixProposal>(
    `/requirements/${requirementId}/fix`,
  );
  return data;
}

/** FORGE-259: the constraint editor's "create" action -- errors (e.g. an
 * unrecognized unit) propagate to the caller rather than being swallowed,
 * unlike the read endpoints above, since a failed write needs to reach
 * the form's own error state. */
export async function createConstraint(
  payload: CreateConstraintPayload,
): Promise<CreateConstraintResult> {
  const { data } = await apiClient.post<CreateConstraintResult>('/requirements/constraints', payload);
  return data;
}
