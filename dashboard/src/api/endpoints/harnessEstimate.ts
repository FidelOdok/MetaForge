import apiClient from '../client';

export interface HarnessJointEntry {
  stepNumber: number;
  jointName: string;
  jointType: string;
  base: string;
  follower: string;
  segmentLengthMm: number;
  cableLengthEstimateMm: number;
}

interface HarnessJointEntryApi {
  step_number: number;
  joint_name: string;
  joint_type: string;
  base: string;
  follower: string;
  segment_length_mm: number;
  cable_length_estimate_mm: number;
}

function fromApiJoint(j: HarnessJointEntryApi): HarnessJointEntry {
  return {
    stepNumber: j.step_number,
    jointName: j.joint_name,
    jointType: j.joint_type,
    base: j.base,
    follower: j.follower,
    segmentLengthMm: j.segment_length_mm,
    cableLengthEstimateMm: j.cable_length_estimate_mm,
  };
}

export interface HarnessEstimateResult {
  workProductId: string;
  joints: HarnessJointEntry[];
}

interface HarnessEstimateApiResponse {
  work_product_id: string;
  joints: HarnessJointEntryApi[];
}

/** FORGE-275: a per-joint cumulative cable-length ESTIMATE (sum of straight
 * segments along the real base->follower kinematic chain, not a routed
 * path) derived from a work product's real committed assembly.joints. */
export async function getHarnessEstimate(workProductId: string): Promise<HarnessEstimateResult> {
  const { data } = await apiClient.get<HarnessEstimateApiResponse>('/wiring/harness-estimate', {
    params: { work_product_id: workProductId },
  });
  return {
    workProductId: data.work_product_id,
    joints: data.joints.map(fromApiJoint),
  };
}
