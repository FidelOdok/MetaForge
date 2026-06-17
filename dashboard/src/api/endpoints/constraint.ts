import apiClient from '../client';

/** Translation delta of a dragged rigid group, in world units (mm). */
export interface DeltaTransform {
  dx: number;
  dy: number;
  dz: number;
}

export interface ConstraintSuggestion {
  parameter: string;
  value: number;
  unit: string;
}

export interface SynthesizeResponse {
  status: 'ok' | 'conflict' | 'noop';
  suggestion: string;
  constraint: ConstraintSuggestion | null;
  conflict_reason: string | null;
}

/**
 * Apply a rigid-group drag delta by asking the agent to synthesize a parametric
 * constraint (MET-519). Tier-1 backend is a stub; later tiers re-solve and
 * stream a new GLB.
 */
export async function synthesizeConstraint(
  groupName: string,
  delta: DeltaTransform,
): Promise<SynthesizeResponse> {
  const { data } = await apiClient.post<SynthesizeResponse>('/constraint/synthesize', {
    group_name: groupName,
    delta,
  });
  return data;
}
