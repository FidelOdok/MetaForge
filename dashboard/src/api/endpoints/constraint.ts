import apiClient from '../client';

/** Single-axis rotation delta of a dragged rigid group (degrees). */
export interface RotationDelta {
  axis: 'x' | 'y' | 'z';
  angle_deg: number;
}

/** Single-axis scale delta of a dragged rigid group (multiplier). */
export interface ScaleDelta {
  axis: 'x' | 'y' | 'z';
  factor: number;
}

/**
 * Delta of a dragged rigid group: translation (mm), or — exactly one of —
 * a single-axis rotation or scale, matching whichever gizmo mode produced it.
 */
export interface DeltaTransform {
  dx: number;
  dy: number;
  dz: number;
  rotation?: RotationDelta;
  scale?: ScaleDelta;
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
