import { useMutation, useQueryClient } from '@tanstack/react-query';
import { booleanCutNodes, type BooleanCutOperation } from '../api/endpoints/twin';
import { useViewerStore } from '../store/viewer-store';
import { twinKeys } from './use-twin';

/**
 * Mutation for the boolean-cut action (MET-612): posts target + cutter to
 * the real CSG endpoint. Invalidates twin queries so the new node shows up
 * in the tree (same call `NodeProposals.tsx` already makes) and closes
 * boolean-cut mode. Re-targeting the 3D viewer at the new node is left to
 * the host page's existing "selected node" auto-loader (`TwinViewerPage`'s
 * MET-505 effect) — callers should set their selected-node id to
 * `result.node.id` from a `.mutate(vars, { onSuccess })` passed at the call
 * site, which drives that loader without a second, duplicate model fetch.
 */
export function useBooleanCut() {
  const queryClient = useQueryClient();
  const closeBooleanCut = useViewerStore((s) => s.closeBooleanCut);

  return useMutation({
    mutationFn: (args: {
      targetNodeId: string;
      cutterNodeId: string;
      operation: BooleanCutOperation;
      resultName?: string;
    }) =>
      booleanCutNodes(args.targetNodeId, args.cutterNodeId, args.operation, args.resultName),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: twinKeys.all });
      closeBooleanCut();
    },
  });
}
