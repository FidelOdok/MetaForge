import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  exportUrdf,
  exportSdf,
  exportUsd,
  exportUrdfAssembly,
  exportSdfAssembly,
  exportUsdAssembly,
  generateRos2Launch,
  getSessionSummary,
  getSessionJoints,
  type UrdfExportRequest,
  type SdfExportRequest,
  type UsdExportRequest,
  type UrdfAssemblyExportRequest,
  type SdfAssemblyExportRequest,
  type UsdAssemblyExportRequest,
  type Ros2LaunchRequest,
} from '../api/endpoints/cad-export';
import { twinKeys } from './use-twin';

/** MET-720/721: single-part export. Doesn't touch the Twin graph (output is
 * a throwaway derived artifact per MET-719), so unlike useBooleanCut there's
 * no query invalidation to do on success. */

export function useExportUrdf() {
  return useMutation({
    mutationFn: (req: UrdfExportRequest) => exportUrdf(req),
  });
}

export function useExportSdf() {
  return useMutation({
    mutationFn: (req: SdfExportRequest) => exportSdf(req),
  });
}

export function useExportUsd() {
  return useMutation({
    mutationFn: (req: UsdExportRequest) => exportUsd(req),
  });
}

/** MET-740: assembly exports DO touch the Twin graph by default
 * (persist=true commits/updates a robot_description work product) —
 * invalidate the node list on a persisted success so it shows up (or its
 * new version reflects) immediately rather than waiting for the next poll. */
function useInvalidateOnPersist() {
  const queryClient = useQueryClient();
  return (data: { robot_description_node_id: string | null }) => {
    if (data.robot_description_node_id) {
      queryClient.invalidateQueries({ queryKey: twinKeys.all });
    }
  };
}

export function useExportUrdfAssembly() {
  const onSuccess = useInvalidateOnPersist();
  return useMutation({
    mutationFn: (req: UrdfAssemblyExportRequest) => exportUrdfAssembly(req),
    onSuccess,
  });
}

export function useExportSdfAssembly() {
  const onSuccess = useInvalidateOnPersist();
  return useMutation({
    mutationFn: (req: SdfAssemblyExportRequest) => exportSdfAssembly(req),
    onSuccess,
  });
}

export function useExportUsdAssembly() {
  const onSuccess = useInvalidateOnPersist();
  return useMutation({
    mutationFn: (req: UsdAssemblyExportRequest) => exportUsdAssembly(req),
    onSuccess,
  });
}

export function useGenerateRos2Launch() {
  return useMutation({
    mutationFn: (req: Ros2LaunchRequest) => generateRos2Launch(req),
  });
}

/** MET-721: fetch a live FreeCAD session's authored objects/joints for the
 * "reuse joints from chat" picker. `enabled` gates the query on the user
 * actually having entered a session_id (this is never auto-discovered). */
export function useSessionSummary(sessionId: string, enabled: boolean) {
  return useQuery({
    queryKey: ['cad-export-session', sessionId],
    queryFn: () => getSessionSummary(sessionId),
    enabled: enabled && sessionId.length > 0,
    retry: false,
  });
}

export function useSessionJoints(sessionId: string, enabled: boolean) {
  return useQuery({
    queryKey: ['cad-export-session-joints', sessionId],
    queryFn: () => getSessionJoints(sessionId),
    enabled: enabled && sessionId.length > 0,
    retry: false,
  });
}
