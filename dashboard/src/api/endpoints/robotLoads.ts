import apiClient from '../client';
import type { JointLoadChainPayload } from '../../lib/robot-statics';

export interface JointLoadResult {
  jointName: string;
  supportedMassKg: number;
  reactionForceN: [number, number, number];
  reactionMomentNMm: [number, number, number];
}

interface JointLoadApiResult {
  joint_name: string;
  supported_mass_kg: number;
  reaction_force_n: [number, number, number];
  reaction_moment_n_mm: [number, number, number];
}

interface JointLoadResponse {
  loads: JointLoadApiResult[];
  worst_joint: JointLoadApiResult;
}

function fromApi(result: JointLoadApiResult): JointLoadResult {
  return {
    jointName: result.joint_name,
    supportedMassKg: result.supported_mass_kg,
    reactionForceN: result.reaction_force_n,
    reactionMomentNMm: result.reaction_moment_n_mm,
  };
}

/** FORGE-283: posts a posed serial chain (from `buildJointLoadChainPayload`)
 * to `calculix.compute_joint_loads` via the gateway and returns every
 * joint's reaction load plus the worst one. */
export async function computeJointLoads(
  payload: JointLoadChainPayload,
): Promise<{ loads: JointLoadResult[]; worstJoint: JointLoadResult }> {
  const { data } = await apiClient.post<JointLoadResponse>('/robot/joint-loads', payload);
  return { loads: data.loads.map(fromApi), worstJoint: fromApi(data.worst_joint) };
}
