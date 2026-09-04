import { useMutation, useQuery } from '@tanstack/react-query';
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

/** MET-720/721: robotics-sim export. Doesn't touch the Twin graph (output is
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

export function useExportUrdfAssembly() {
  return useMutation({
    mutationFn: (req: UrdfAssemblyExportRequest) => exportUrdfAssembly(req),
  });
}

export function useExportSdfAssembly() {
  return useMutation({
    mutationFn: (req: SdfAssemblyExportRequest) => exportSdfAssembly(req),
  });
}

export function useExportUsdAssembly() {
  return useMutation({
    mutationFn: (req: UsdAssemblyExportRequest) => exportUsdAssembly(req),
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
