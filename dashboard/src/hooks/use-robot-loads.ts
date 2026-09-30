import { useMutation } from '@tanstack/react-query';
import { computeJointLoads } from '../api/endpoints/robotLoads';

/** FORGE-283: "Use as load case" -- compute reaction loads at every joint
 * of the robot's current pose. */
export function useComputeJointLoads() {
  return useMutation({ mutationFn: computeJointLoads });
}
