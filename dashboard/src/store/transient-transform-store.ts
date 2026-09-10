import { create } from 'zustand';

/** A delta applied to a rigid group: translation (mm) or per-axis rotation (rad) or scale (multiplier). */
export type Vec3 = [number, number, number];

/** Gizmo interaction mode — mirrors drei's `TransformControls` mode prop. */
export type TransformMode = 'translate' | 'rotate' | 'scale';

const ZERO: Vec3 = [0, 0, 0];
const IDENTITY_SCALE: Vec3 = [1, 1, 1];

/**
 * Client-side transient state for interactive rigid-group manipulation
 * (MET-519). Dragging a group applies a *local* delta transform that never
 * touches the server's GLB or constraint graph; it lives here until the user
 * Applies (→ constraint synthesis) or Reverts (→ discard).
 *
 * Phase 2 (MET-611) adds rotate/scale alongside the original translate-only
 * delta — all three deltas are tracked independently so switching modes
 * mid-manipulation never loses a pending change in another mode.
 */
interface TransientTransformState {
  /** Name of the currently-selected rigid group, or null. */
  selectedGroup: string | null;
  /** Active gizmo mode. */
  mode: TransformMode;
  /** Translation delta of the selected group since selection (mm). */
  delta: Vec3;
  /** Rotation delta of the selected group since selection (radians, per-axis Euler). */
  rotationDelta: Vec3;
  /** Scale delta of the selected group since selection (per-axis multiplier). */
  scaleDelta: Vec3;
  /** True when the active mode's delta is non-identity (Apply/Revert enabled). */
  isDirty: boolean;

  /** Select a group (or null to deselect). Clears any pending deltas. */
  selectGroup: (groupName: string | null) => void;
  /** Switch gizmo mode. Does not clear pending deltas in other modes. */
  setMode: (mode: TransformMode) => void;
  /** Set the absolute translation delta for the selected group. */
  setDelta: (delta: Vec3) => void;
  /** Set the absolute rotation delta (radians) for the selected group. */
  setRotationDelta: (rotationDelta: Vec3) => void;
  /** Set the absolute scale delta (multiplier) for the selected group. */
  setScaleDelta: (scaleDelta: Vec3) => void;
  /** Revert: discard all pending deltas and keep the selection. */
  revert: () => void;
  /** Clear everything after a successful Apply (delta committed server-side). */
  clearAfterApply: () => void;
}

function isZero(v: Vec3): boolean {
  return v[0] === 0 && v[1] === 0 && v[2] === 0;
}

function isIdentityScale(v: Vec3): boolean {
  return v[0] === 1 && v[1] === 1 && v[2] === 1;
}

function isDirtyFor(mode: TransformMode, delta: Vec3, rotationDelta: Vec3, scaleDelta: Vec3): boolean {
  if (mode === 'translate') return !isZero(delta);
  if (mode === 'rotate') return !isZero(rotationDelta);
  return !isIdentityScale(scaleDelta);
}

export const useTransientTransform = create<TransientTransformState>((set, get) => ({
  selectedGroup: null,
  mode: 'translate',
  delta: ZERO,
  rotationDelta: ZERO,
  scaleDelta: IDENTITY_SCALE,
  isDirty: false,

  selectGroup: (groupName) =>
    set({
      selectedGroup: groupName,
      delta: ZERO,
      rotationDelta: ZERO,
      scaleDelta: IDENTITY_SCALE,
      isDirty: false,
    }),

  setMode: (mode) => {
    const { delta, rotationDelta, scaleDelta } = get();
    set({ mode, isDirty: isDirtyFor(mode, delta, rotationDelta, scaleDelta) });
  },

  setDelta: (delta) => {
    // No-op if nothing is selected — a delta has no meaning without a group.
    if (get().selectedGroup === null) return;
    const { mode, rotationDelta, scaleDelta } = get();
    set({ delta, isDirty: isDirtyFor(mode, delta, rotationDelta, scaleDelta) });
  },

  setRotationDelta: (rotationDelta) => {
    if (get().selectedGroup === null) return;
    const { mode, delta, scaleDelta } = get();
    set({ rotationDelta, isDirty: isDirtyFor(mode, delta, rotationDelta, scaleDelta) });
  },

  setScaleDelta: (scaleDelta) => {
    if (get().selectedGroup === null) return;
    const { mode, delta, rotationDelta } = get();
    set({ scaleDelta, isDirty: isDirtyFor(mode, delta, rotationDelta, scaleDelta) });
  },

  revert: () =>
    set({ delta: ZERO, rotationDelta: ZERO, scaleDelta: IDENTITY_SCALE, isDirty: false }),

  clearAfterApply: () =>
    set({
      selectedGroup: null,
      delta: ZERO,
      rotationDelta: ZERO,
      scaleDelta: IDENTITY_SCALE,
      isDirty: false,
    }),
}));
