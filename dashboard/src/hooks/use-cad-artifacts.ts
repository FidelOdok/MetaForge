import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getNodeArtifacts, requestExactBuild, requestStepExport } from '../api/endpoints/cad-artifacts';
import { isSampleMode } from '../lib/sample-workspace';

/** CDE slice 6: one CAD revision's geometry and export states, kept fresh while a job runs. */
export const cadArtifactKeys = {
  node: (nodeId: string) => ['cad-artifacts', nodeId] as const,
};

export function useNodeArtifacts(nodeId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: cadArtifactKeys.node(nodeId ?? ''),
    queryFn: () => getNodeArtifacts(nodeId as string),
    enabled: Boolean(nodeId) && enabled && !isSampleMode(),
    retry: false,
    // A queued export or a running build changes state in the background.
    refetchInterval: (query) => {
      const data = query.state.data;
      const pending = data?.exports.some((e) => e.state === 'QUEUED') || data?.geometry_state === 'validation_pending';
      return pending ? 2000 : false;
    },
  });
}

export function useExportStep(nodeId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (critical: boolean = false) => requestStepExport(nodeId, critical),
    onSettled: () => queryClient.invalidateQueries({ queryKey: cadArtifactKeys.node(nodeId) }),
  });
}

export function useExactBuild(nodeId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (args: { itemKey: string; projectId: string; baseRevision?: number | null }) =>
      requestExactBuild(args.itemKey, args.projectId, args.baseRevision),
    onSettled: () => queryClient.invalidateQueries({ queryKey: cadArtifactKeys.node(nodeId) }),
  });
}
