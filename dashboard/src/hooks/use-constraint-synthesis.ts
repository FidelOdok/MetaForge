import { useMutation } from '@tanstack/react-query';
import { synthesizeConstraint, type DeltaTransform } from '../api/endpoints/constraint';

/**
 * Mutation for the rigid-group *Apply* action (MET-519): posts the group's
 * drag delta to the constraint-synthesis endpoint and returns the suggestion /
 * conflict result. The viewer uses `isPending` to disable Apply while in flight.
 */
export function useSynthesizeConstraint() {
  return useMutation({
    mutationFn: (args: { groupName: string; delta: DeltaTransform }) =>
      synthesizeConstraint(args.groupName, args.delta),
  });
}
