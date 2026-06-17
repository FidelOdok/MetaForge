import { create } from 'zustand';

/** A translation delta applied to a rigid group, in world units (mm). */
export type Vec3 = [number, number, number];

const ZERO: Vec3 = [0, 0, 0];

/**
 * Client-side transient state for interactive rigid-group manipulation
 * (MET-519, Tier 1). Dragging a group applies a *local* delta transform that
 * never touches the server's GLB or constraint graph; it lives here until the
 * user Applies (→ constraint synthesis) or Reverts (→ discard).
 *
 * Phase 1.5 MVP tracks a translation delta only; rotation is Phase 2.
 */
interface TransientTransformState {
  /** Name of the currently-selected rigid group, or null. */
  selectedGroup: string | null;
  /** Translation delta of the selected group since selection. */
  delta: Vec3;
  /** True when the selected group has a non-zero delta (Apply/Revert enabled). */
  isDirty: boolean;

  /** Select a group (or null to deselect). Clears any pending delta. */
  selectGroup: (groupName: string | null) => void;
  /** Set the absolute translation delta for the selected group. */
  setDelta: (delta: Vec3) => void;
  /** Revert: discard the delta and keep the selection. */
  revert: () => void;
  /** Clear everything after a successful Apply (delta committed server-side). */
  clearAfterApply: () => void;
}

function isZero(v: Vec3): boolean {
  return v[0] === 0 && v[1] === 0 && v[2] === 0;
}

export const useTransientTransform = create<TransientTransformState>((set, get) => ({
  selectedGroup: null,
  delta: ZERO,
  isDirty: false,

  selectGroup: (groupName) =>
    set({ selectedGroup: groupName, delta: ZERO, isDirty: false }),

  setDelta: (delta) => {
    // No-op if nothing is selected — a delta has no meaning without a group.
    if (get().selectedGroup === null) return;
    set({ delta, isDirty: !isZero(delta) });
  },

  revert: () => set({ delta: ZERO, isDirty: false }),

  clearAfterApply: () => set({ selectedGroup: null, delta: ZERO, isDirty: false }),
}));
