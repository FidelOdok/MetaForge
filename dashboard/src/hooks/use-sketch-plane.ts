import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { create } from 'zustand';
import {
  archiveSketchPlane,
  confirmSketchPlane,
  getSketchPlane,
  undoSketchPlane,
  reassignSketchPlane,
  type ConfirmBody,
  type PlaneChangeResult,
  type ReassignBody,
  type ReassignPreview,
  type SketchPlaneView,
  type ViewHint,
} from '../api/endpoints/sketchPlanes';
import { getCurrentSketch } from '../api/endpoints/sketches';
import { twinKeys } from './use-twin';
import { generateId } from '../utils/id';

/** Query keys for sketch planes (FORGE-790). */
export const sketchPlaneKeys = {
  plane: (projectId: string, sketchKey: string) => ['sketch-plane', projectId, sketchKey] as const,
  strokes: (projectId: string, sketchKey: string) => ['sketch-plane', projectId, sketchKey, 'strokes'] as const,
};

/** The sketch's current plane. Refetched only on a plane change: the camera never touches it. */
export function useSketchPlane(projectId: string | null | undefined, sketchKey: string | null | undefined) {
  return useQuery({
    queryKey: sketchPlaneKeys.plane(projectId ?? '', sketchKey ?? ''),
    queryFn: ({ signal }) => getSketchPlane(projectId!, sketchKey!, signal),
    enabled: !!projectId && !!sketchKey,
    staleTime: 30_000,
    retry: false,
  });
}

/** The head revision of one sketch, with its strokes (for 3D placement). */
export function useSketchStrokes(projectId: string | null | undefined, sketchKey: string | null | undefined) {
  return useQuery({
    queryKey: sketchPlaneKeys.strokes(projectId ?? '', sketchKey ?? ''),
    queryFn: ({ signal }) => getCurrentSketch(projectId!, signal, sketchKey),
    enabled: !!projectId && !!sketchKey,
    staleTime: 30_000,
    retry: false,
  });
}

/** Confirm, reassign, undo and archive, each with a fresh op id. */
export function useSketchPlaneActions(projectId: string, sketchKey: string | null | undefined) {
  const qc = useQueryClient();
  const key = sketchPlaneKeys.plane(projectId, sketchKey ?? '');
  const settle = (result: PlaneChangeResult) => {
    const view: SketchPlaneView = result;
    qc.setQueryData(key, view);
    if (result.sketch_revision_written) {
      void qc.invalidateQueries({ queryKey: sketchPlaneKeys.strokes(projectId, sketchKey ?? '') });
    }
    void qc.invalidateQueries({ queryKey: twinKeys.all });
    useSketchPlaneUi.getState().setPreview(null);
  };
  const need = () => {
    if (!sketchKey) throw new Error('Save the sketch first');
    return sketchKey;
  };
  const confirm = useMutation({
    mutationFn: (body: Omit<ConfirmBody, 'op_id'>) => confirmSketchPlane(projectId, need(), { op_id: generateId(), ...body }),
    onSuccess: settle,
  });
  const reassign = useMutation({
    mutationFn: (body: Omit<ReassignBody, 'op_id'>) => reassignSketchPlane(projectId, need(), { op_id: generateId(), ...body }),
    onSuccess: settle,
  });
  const undo = useMutation({
    mutationFn: (base?: string | null) => undoSketchPlane(projectId, need(), { op_id: generateId(), base_revision: base ?? null }),
    onSuccess: settle,
  });
  const archive = useMutation({
    mutationFn: (base?: string | null) => archiveSketchPlane(projectId, need(), { op_id: generateId(), base_revision: base ?? null }),
    onSuccess: settle,
  });
  return { confirm, reassign, undo, archive };
}

/** View-only plane controls in Engineering: never stored, never sent. */
interface SketchPlaneUi {
  showGrid: boolean;
  showAxes: boolean;
  /** A reassign preview to draw over the sketch, until committed or dismissed. */
  preview: ReassignPreview | null;
  /** Registered from inside the 3D canvas: turn the camera to face the sketch. Camera only. */
  normalToSketch: (() => void) | null;
  /** Registered from inside the 3D canvas: the orthographic view the camera is on, if any.
   * Read once when a proposal is asked for; a hint only, it never changes a plane. */
  viewHint: (() => ViewHint | null) | null;
  setShowGrid: (v: boolean) => void;
  setShowAxes: (v: boolean) => void;
  setPreview: (p: ReassignPreview | null) => void;
  registerNormalToSketch: (fn: (() => void) | null) => void;
  registerViewHint: (fn: (() => ViewHint | null) | null) => void;
}

export const useSketchPlaneUi = create<SketchPlaneUi>((set) => ({
  showGrid: true,
  showAxes: true,
  preview: null,
  normalToSketch: null,
  viewHint: null,
  registerViewHint: (viewHint) => set({ viewHint }),
  setShowGrid: (showGrid) => set({ showGrid }),
  setShowAxes: (showAxes) => set({ showAxes }),
  setPreview: (preview) => set({ preview }),
  registerNormalToSketch: (normalToSketch) => set({ normalToSketch }),
}));
