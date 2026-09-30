import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getTwinNodes,
  getTwinNode,
  getTwinRelationships,
  getNodeVersionHistory,
  getRevisionDiff,
  getGeometryDiff,
  approveSketch,
  updateAssemblyJoints,
  iterateWorkProduct,
} from '../api/endpoints/twin';
import type { AssemblyDescription } from '../types/twin';
import { upsertNamedPose, type PoseValues } from '../lib/robot-poses';

export const twinKeys = {
  all: ['twin'] as const,
  node: (id: string) => [...twinKeys.all, id] as const,
  relationships: (projectId?: string) => ['twin', 'relationships', projectId ?? ''] as const,
};

// MET-539: keep the Twin view live by polling. React Query only polls while the
// tab is focused (refetchIntervalInBackground defaults to false), so this picks
// up imports/edits/deletes within a few seconds without burning requests when
// the dashboard isn't on screen. staleTime is kept below the interval so focus
// refetches stay fresh too.
const TWIN_NODES_POLL_MS = 10_000;
const TWIN_NODE_POLL_MS = 15_000;
const TWIN_RELATIONSHIPS_POLL_MS = 15_000;

export function useTwinNodes(projectId?: string) {
  return useQuery({
    // MET-491: project scope is part of the cache key so switching
    // projects refetches the scoped node list.
    queryKey: [...twinKeys.all, 'project', projectId ?? ''] as const,
    queryFn: () => getTwinNodes(projectId || undefined),
    staleTime: TWIN_NODES_POLL_MS,
    refetchInterval: TWIN_NODES_POLL_MS,
  });
}

export function useTwinNode(id: string | undefined) {
  return useQuery({
    queryKey: twinKeys.node(id ?? ''),
    queryFn: () => getTwinNode(id!),
    enabled: !!id,
    staleTime: TWIN_NODE_POLL_MS,
    refetchInterval: TWIN_NODE_POLL_MS,
  });
}

export function useTwinRelationships(projectId?: string) {
  return useQuery({
    // MET-491/MET-653: project scope is part of the cache key so switching
    // projects refetches the scoped relationship list (mirrors useTwinNodes).
    queryKey: twinKeys.relationships(projectId),
    queryFn: () => getTwinRelationships(projectId),
    staleTime: TWIN_RELATIONSHIPS_POLL_MS,
    refetchInterval: TWIN_RELATIONSHIPS_POLL_MS,
  });
}

export function useNodeVersionHistory(nodeId: string | undefined) {
  return useQuery({
    queryKey: [...twinKeys.all, nodeId, 'versions'] as const,
    queryFn: () => getNodeVersionHistory(nodeId!),
    enabled: !!nodeId,
    staleTime: 15_000,
  });
}

/** FORGE-301: metadata diff between two explicitly-picked revisions of the
 * SAME node. Disabled until both revisions are chosen (there's no sensible
 * default pair -- unlike history/geometry-diff, this needs user input). */
export function useRevisionDiff(
  nodeId: string | undefined,
  revisionA: number | undefined,
  revisionB: number | undefined,
) {
  return useQuery({
    queryKey: [...twinKeys.all, nodeId, 'diff', revisionA ?? 0, revisionB ?? 0] as const,
    queryFn: () => getRevisionDiff(nodeId!, revisionA!, revisionB!),
    enabled: !!nodeId && !!revisionA && !!revisionB && revisionA !== revisionB,
    staleTime: 15_000,
  });
}

/** FORGE-301: real volume/area/bounding-box delta vs. a SUPERSEDES
 * predecessor. `null` (a 404) is the expected, common answer for a work
 * product with no prior version -- not a loading/error state. */
export function useGeometryDiff(nodeId: string | undefined) {
  return useQuery({
    queryKey: [...twinKeys.all, nodeId, 'geometry-diff'] as const,
    queryFn: () => getGeometryDiff(nodeId!),
    enabled: !!nodeId,
    staleTime: 15_000,
  });
}

/** FORGE-271: edit the joint list on an already-committed assembly node —
 * no live FreeCAD session or re-export needed, unlike the export panel's
 * own joint form. Invalidates the node + list so the Assembly tab's
 * joint list and any part picker reflect the change immediately. */
export function useUpdateAssemblyJoints() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      nodeId,
      joints,
    }: {
      nodeId: string;
      joints: AssemblyDescription['joints'];
    }) => updateAssemblyJoints(nodeId, joints),
    onSuccess: (_data, { nodeId }) => {
      queryClient.invalidateQueries({ queryKey: twinKeys.node(nodeId) });
      queryClient.invalidateQueries({ queryKey: twinKeys.all });
    },
  });
}

/** Follow-up to MET-740/747: human sign-off on a design_sketch node.
 * Invalidates the node + its version history so the approval badge and the
 * new revision it creates show up immediately rather than on the next poll. */
export function useApproveSketch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ nodeId, approvedBy }: { nodeId: string; approvedBy?: string }) =>
      approveSketch(nodeId, approvedBy),
    onSuccess: (_data, { nodeId }) => {
      queryClient.invalidateQueries({ queryKey: twinKeys.node(nodeId) });
      queryClient.invalidateQueries({ queryKey: [...twinKeys.all, nodeId, 'versions'] });
      queryClient.invalidateQueries({ queryKey: twinKeys.all });
    },
  });
}

/** FORGE-250: "Save current pose" — merges one named pose into the
 * robot_description node's existing `metadata.poses` (never overwriting
 * other saved poses) via the generic iterate/revision endpoint, so the
 * save survives a reload and shows up in the node's version history
 * (acceptance criteria). Invalidates the same node + versions queries as
 * `useApproveSketch` above, for the same reason. */
export function useSaveRobotPose() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      nodeId,
      existingPoses,
      poseName,
      values,
    }: {
      nodeId: string;
      existingPoses: Record<string, PoseValues> | undefined;
      poseName: string;
      values: PoseValues;
    }) =>
      iterateWorkProduct(nodeId, `Saved pose "${poseName}"`, {
        poses: upsertNamedPose(existingPoses, poseName, values),
      }),
    onSuccess: (_data, { nodeId }) => {
      queryClient.invalidateQueries({ queryKey: twinKeys.node(nodeId) });
      queryClient.invalidateQueries({ queryKey: [...twinKeys.all, nodeId, 'versions'] });
      queryClient.invalidateQueries({ queryKey: twinKeys.all });
    },
  });
}
