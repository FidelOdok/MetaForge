import { useMutation } from '@tanstack/react-query';
import {
  exportUrdf,
  exportSdf,
  exportUsd,
  type UrdfExportRequest,
  type SdfExportRequest,
  type UsdExportRequest,
} from '../api/endpoints/cad-export';

/** MET-720: single-part robotics-sim export. Doesn't touch the Twin graph
 * (output is a throwaway derived artifact per MET-719), so unlike
 * useBooleanCut there's no query invalidation to do on success. */

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
