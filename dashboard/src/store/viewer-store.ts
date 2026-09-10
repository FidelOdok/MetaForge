import { create } from 'zustand';
import type { ExplodeDirection, ModelManifest } from '../types/viewer';

/** Boolean-cut mode (MET-612): 'idle' (hidden) → 'picking-cutter' (choosing a
 * sibling node) → 'ready' (cutter loaded, Hole/Group enabled). */
export type BooleanCutMode = 'idle' | 'picking-cutter' | 'ready';

interface BooleanCutState {
  mode: BooleanCutMode;
  targetNodeId: string | null;
  cutterNodeId: string | null;
  cutterGlbUrl: string | null;
  cutterManifest: ModelManifest | null;
  cutting: boolean;
}

const BOOLEAN_CUT_IDLE: BooleanCutState = {
  mode: 'idle',
  targetNodeId: null,
  cutterNodeId: null,
  cutterGlbUrl: null,
  cutterManifest: null,
  cutting: false,
};

/** The loaded model's actual world-space extent (MET-620), computed once from
 * its GLB scene. Lets the camera fit itself to whatever is actually loaded —
 * previously the camera always started at a hardcoded generic position
 * regardless of the model's real size/position, and the ground grid was a
 * fixed size unrelated to it — so an orbit could look like the (large,
 * fixed-size) grid was rotating rather than the (possibly off-center, wrongly
 * scaled) object. */
export interface ModelBounds {
  center: [number, number, number];
  /** Half the bounding-box diagonal — used to scale camera distance and grid size. */
  radius: number;
  /** The model's lowest point — where the ground plane should actually sit. */
  groundY: number;
}

interface ViewerState {
  glbUrl: string | null;
  manifest: ModelManifest | null;
  selectedMeshName: string | null;
  hiddenMeshes: Set<string>;
  explodeFactor: number;
  explodeDirection: ExplodeDirection;
  animating: boolean;
  viewMode: '3d' | 'graph';
  /** Callback registered by the R3F canvas to reset the camera. */
  _cameraResetFn: (() => void) | null;
  /** Set by SceneContents once the primary model's GLB scene has loaded. */
  modelBounds: ModelBounds | null;
  setModelBounds: (bounds: ModelBounds | null) => void;

  loadModel: (glbUrl: string, manifest: ModelManifest) => void;
  /** MET-683: clear the loaded model WITHOUT leaving 3D view mode (unlike
   * `reset`, which also flips viewMode back to 'graph') -- used before
   * loading a newly-selected node so a failed load doesn't leave the
   * PREVIOUS node's geometry on screen under the new node's breadcrumb. */
  clearModel: () => void;
  selectPart: (meshName: string | null) => void;
  toggleVisibility: (meshName: string) => void;
  setExplodeFactor: (factor: number) => void;
  toggleExplodeDirection: () => void;
  toggleExplode: () => void;
  resetExplode: () => void;
  setAnimating: (animating: boolean) => void;
  setViewMode: (mode: '3d' | 'graph') => void;
  reset: () => void;
  /** Register the camera reset callback from inside the R3F Canvas. */
  registerCameraReset: (fn: () => void) => void;
  /** Trigger the camera reset (called from outside the Canvas). */
  resetCamera: () => void;

  /** Additive boolean-cut slice (MET-612) — never touches the primary
   * glbUrl/manifest/selectedMeshName state above. */
  booleanCut: BooleanCutState;
  /** Enter picking-cutter mode for the currently-open node. */
  openBooleanCut: (targetNodeId: string) => void;
  /** A cutter node's model finished loading — advance to 'ready'. */
  setBooleanCutCutter: (
    cutterNodeId: string,
    cutterGlbUrl: string,
    cutterManifest: ModelManifest,
  ) => void;
  /** Clear the chosen cutter without leaving boolean-cut mode. */
  clearBooleanCutCutter: () => void;
  setBooleanCutting: (cutting: boolean) => void;
  closeBooleanCut: () => void;
}

export const useViewerStore = create<ViewerState>((set, get) => ({
  glbUrl: null,
  manifest: null,
  selectedMeshName: null,
  hiddenMeshes: new Set<string>(),
  explodeFactor: 0,
  explodeDirection: 'radial' as ExplodeDirection,
  animating: false,
  viewMode: 'graph',
  _cameraResetFn: null,
  modelBounds: null,
  setModelBounds: (bounds) => set({ modelBounds: bounds }),

  loadModel: (glbUrl, manifest) =>
    set({
      glbUrl,
      manifest,
      selectedMeshName: null,
      hiddenMeshes: new Set(),
      explodeFactor: 0,
      viewMode: '3d',
      modelBounds: null,
    }),

  clearModel: () =>
    set({
      glbUrl: null,
      manifest: null,
      selectedMeshName: null,
      hiddenMeshes: new Set(),
      explodeFactor: 0,
      modelBounds: null,
    }),

  selectPart: (meshName) => set({ selectedMeshName: meshName }),

  toggleVisibility: (meshName) => {
    const { hiddenMeshes } = get();
    const next = new Set(hiddenMeshes);
    if (next.has(meshName)) {
      next.delete(meshName);
    } else {
      next.add(meshName);
    }
    set({ hiddenMeshes: next });
  },

  setExplodeFactor: (factor) => set({ explodeFactor: Math.max(0, Math.min(100, factor)) }),

  toggleExplodeDirection: () =>
    set((state) => ({
      explodeDirection: state.explodeDirection === 'radial' ? 'axial' : 'radial',
    })),

  toggleExplode: () => {
    const current = get().explodeFactor;
    set({ explodeFactor: current > 0 ? 0 : 100, animating: true });
  },

  resetExplode: () => set({ explodeFactor: 0, animating: true }),

  setAnimating: (animating) => set({ animating }),

  setViewMode: (mode) => set({ viewMode: mode }),

  reset: () =>
    set({
      glbUrl: null,
      manifest: null,
      selectedMeshName: null,
      hiddenMeshes: new Set(),
      explodeFactor: 0,
      viewMode: 'graph',
      modelBounds: null,
    }),

  registerCameraReset: (fn) => set({ _cameraResetFn: fn }),

  resetCamera: () => {
    const fn = get()._cameraResetFn;
    if (fn) fn();
  },

  booleanCut: BOOLEAN_CUT_IDLE,

  openBooleanCut: (targetNodeId) =>
    set({ booleanCut: { ...BOOLEAN_CUT_IDLE, mode: 'picking-cutter', targetNodeId } }),

  setBooleanCutCutter: (cutterNodeId, cutterGlbUrl, cutterManifest) =>
    set((state) => ({
      booleanCut: {
        ...state.booleanCut,
        mode: 'ready',
        cutterNodeId,
        cutterGlbUrl,
        cutterManifest,
      },
    })),

  clearBooleanCutCutter: () =>
    set((state) => ({
      booleanCut: {
        ...state.booleanCut,
        mode: 'picking-cutter',
        cutterNodeId: null,
        cutterGlbUrl: null,
        cutterManifest: null,
      },
    })),

  setBooleanCutting: (cutting) =>
    set((state) => ({ booleanCut: { ...state.booleanCut, cutting } })),

  closeBooleanCut: () => set({ booleanCut: BOOLEAN_CUT_IDLE }),
}));
