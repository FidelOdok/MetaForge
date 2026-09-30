import { create } from 'zustand';
import type { ExplodeDirection, ModelManifest } from '../types/viewer';
import type { JointLoadChainPayload } from '../lib/robot-statics';

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

/** A robot_description node's movable joints, serialized out of the parsed
 * URDFRobot so the joint-slider overlay (an HTML sibling of the Canvas) can
 * render controls without needing the live Three.js robot object itself. */
export interface RobotJointInfo {
  name: string;
  lower: number;
  upper: number;
  initial: number;
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

  /** A robot_description node loaded into the SAME main viewer/Canvas as
   * the GLB path (mutually exclusive with glbUrl/manifest) — the direct
   * "View Robot" consolidation: one Canvas, one set of controls, dispatched
   * on the selected node's wp_type instead of a second floating popup
   * Canvas (MET-747). RobotSceneContents (a Canvas child) does the actual
   * URDF fetch/parse; this slice only carries the node id plus the
   * UI-facing state (joints, values, physics toggle) that the joint-slider
   * overlay — a Canvas *sibling*, so it can't read Three.js objects
   * directly — needs to render itself.
   */
  robotDescription: { nodeId: string } | null;
  robotJoints: RobotJointInfo[] | null;
  robotJointValues: Record<string, number>;
  robotPhysicsEnabled: boolean;
  robotError: string | null;
  loadRobotDescription: (nodeId: string) => void;
  clearRobotDescription: () => void;
  setRobotJoints: (joints: RobotJointInfo[]) => void;
  setRobotJointValue: (name: string, value: number) => void;
  setRobotPhysicsEnabled: (enabled: boolean) => void;
  setRobotError: (message: string | null) => void;

  /** FORGE-250: name of the joint currently hovered by drag-to-pose (an
   * HTML sibling can't read the Canvas-child's raycast hit directly, same
   * reason `_computeJointLoadChainFn` exists below), and whether a drag is
   * actively in progress (used to disable preset buttons + sliders while
   * dragging, matching "disabled in physics mode" for the same reason:
   * two things fighting over robotJointValues at once). */
  robotHoveredJoint: string | null;
  robotDragging: boolean;
  setRobotHoveredJoint: (jointName: string | null) => void;
  setRobotDragging: (dragging: boolean) => void;

  /** FORGE-250: imperative bridge so drag-to-pose can suspend/restore
   * `<OrbitControls>` the instant a drag starts/ends -- a React-state round
   * trip through `robotDragging` above would lose a frame (OrbitControls'
   * own pointerdown listener fires in the same event before a re-render
   * could flip its `enabled` prop), same rationale as `_cameraResetFn`. */
  _orbitControlsEnabledSetterFn: ((enabled: boolean) => void) | null;
  registerOrbitControlsEnabledSetter: (fn: ((enabled: boolean) => void) | null) => void;
  setOrbitControlsEnabled: (enabled: boolean) => void;

  /** FORGE-283: same Canvas-child-registers/HTML-sibling-calls pattern as
   * `_cameraResetFn` above -- RobotSceneContents owns the live URDFRobot
   * object (never put directly in this store) and registers a closure that
   * reads its current world-posed transforms; RobotControlsOverlay's "Use
   * as load case" button calls it on click, at the pose the user is
   * looking at right then, rather than this store re-deriving a plain
   * payload on every joint-slider tick nobody has asked to use yet. */
  _computeJointLoadChainFn: ((jointNames: string[]) => JointLoadChainPayload | null) | null;
  registerJointLoadChainCompute: (
    fn: ((jointNames: string[]) => JointLoadChainPayload | null) | null,
  ) => void;
  computeJointLoadChain: (jointNames: string[]) => JointLoadChainPayload | null;

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

  robotDescription: null,
  robotJoints: null,
  robotJointValues: {},
  robotPhysicsEnabled: false,
  robotError: null,

  loadRobotDescription: (nodeId) =>
    set({
      robotDescription: { nodeId },
      robotJoints: null,
      robotJointValues: {},
      robotPhysicsEnabled: false,
      robotHoveredJoint: null,
      robotDragging: false,
      robotError: null,
      viewMode: '3d',
      // Mutually exclusive with the GLB path — same Canvas, one at a time.
      glbUrl: null,
      manifest: null,
      selectedMeshName: null,
      hiddenMeshes: new Set(),
      explodeFactor: 0,
      modelBounds: null,
    }),

  clearRobotDescription: () =>
    set({
      robotDescription: null,
      robotJoints: null,
      robotJointValues: {},
      robotPhysicsEnabled: false,
      robotHoveredJoint: null,
      robotDragging: false,
      robotError: null,
    }),

  setRobotJoints: (joints) =>
    set({
      robotJoints: joints,
      robotJointValues: Object.fromEntries(joints.map((j) => [j.name, j.initial])),
    }),

  setRobotJointValue: (name, value) =>
    set((state) => ({ robotJointValues: { ...state.robotJointValues, [name]: value } })),

  setRobotPhysicsEnabled: (enabled) => set({ robotPhysicsEnabled: enabled }),

  setRobotError: (message) => set({ robotError: message }),

  robotHoveredJoint: null,
  robotDragging: false,
  setRobotHoveredJoint: (jointName) => set({ robotHoveredJoint: jointName }),
  setRobotDragging: (dragging) => set({ robotDragging: dragging }),

  _orbitControlsEnabledSetterFn: null,
  registerOrbitControlsEnabledSetter: (fn) => set({ _orbitControlsEnabledSetterFn: fn }),
  setOrbitControlsEnabled: (enabled) => {
    const fn = get()._orbitControlsEnabledSetterFn;
    if (fn) fn(enabled);
  },

  _computeJointLoadChainFn: null,
  registerJointLoadChainCompute: (fn) => set({ _computeJointLoadChainFn: fn }),
  computeJointLoadChain: (jointNames) => {
    const fn = get()._computeJointLoadChainFn;
    return fn ? fn(jointNames) : null;
  },

  loadModel: (glbUrl, manifest) =>
    set({
      glbUrl,
      manifest,
      selectedMeshName: null,
      hiddenMeshes: new Set(),
      explodeFactor: 0,
      viewMode: '3d',
      modelBounds: null,
      // Mutually exclusive with the robot-description path.
      robotDescription: null,
      robotJoints: null,
      robotJointValues: {},
      robotPhysicsEnabled: false,
      robotHoveredJoint: null,
      robotDragging: false,
      robotError: null,
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
      robotDescription: null,
      robotJoints: null,
      robotJointValues: {},
      robotPhysicsEnabled: false,
      robotHoveredJoint: null,
      robotDragging: false,
      robotError: null,
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
