import type { RobotJointInfo } from '../store/viewer-store';

/** Joint name -> angle/position value, the same shape as viewer-store's
 * `robotJointValues`. */
export type PoseValues = Record<string, number>;

/** A named pose as persisted on a robot_description node's
 * `metadata.poses` (see docs/twin_schema.md). */
export interface NamedPose {
  name: string;
  values: PoseValues;
}

/** Clamp every joint's value to its URDF limit (continuous joints are
 * normalized to [-pi, pi] upstream in RobotSceneContents, so this clamps
 * those too -- matches the acceptance criterion "joint values are clamped
 * to URDF limits when present"). Missing joints fall back to their current
 * initial value rather than being dropped. */
export function clampToJointLimits(joints: RobotJointInfo[], values: PoseValues): PoseValues {
  const clamped: PoseValues = {};
  for (const j of joints) {
    const v = values[j.name] ?? j.initial;
    clamped[j.name] = Math.min(j.upper, Math.max(j.lower, v));
  }
  return clamped;
}

export function zeroPose(joints: RobotJointInfo[]): PoseValues {
  return Object.fromEntries(joints.map((j) => [j.name, 0]));
}

/** No separate canonical "home" pose exists anywhere in this codebase (no
 * URDF <home> convention, no stored default anywhere) -- defined here as
 * the midpoint of each joint's exported range, a standard robotics
 * convention for a neutral "ready" stance when no explicit home is
 * authored. Distinct from Zero whenever a joint's range isn't centered
 * on 0. */
export function homePose(joints: RobotJointInfo[]): PoseValues {
  return Object.fromEntries(joints.map((j) => [j.name, (j.lower + j.upper) / 2]));
}

export function minPose(joints: RobotJointInfo[]): PoseValues {
  return Object.fromEntries(joints.map((j) => [j.name, j.lower]));
}

export function maxPose(joints: RobotJointInfo[]): PoseValues {
  return Object.fromEntries(joints.map((j) => [j.name, j.upper]));
}

/** Add or overwrite one named pose in a robot_description's saved-pose map
 * without touching any other saved pose. */
export function upsertNamedPose(
  existing: Record<string, PoseValues> | undefined,
  name: string,
  values: PoseValues,
): Record<string, PoseValues> {
  return { ...(existing ?? {}), [name]: values };
}

/** Standard ease-in-out cubic, t in [0, 1]. */
export function easeInOutCubic(t: number): number {
  const clamped = Math.min(1, Math.max(0, t));
  return clamped < 0.5 ? 4 * clamped ** 3 : 1 - (-2 * clamped + 2) ** 3 / 2;
}

/** Per-joint linear interpolation between `start` and `target`, eased by
 * `easeInOutCubic`. A joint missing from `start` snaps straight to its
 * target (nothing to interpolate from). */
export function interpolatePose(start: PoseValues, target: PoseValues, t: number): PoseValues {
  const eased = easeInOutCubic(t);
  const result: PoseValues = {};
  for (const [name, to] of Object.entries(target)) {
    const from = start[name] ?? to;
    result[name] = from + (to - from) * eased;
  }
  return result;
}

const DEFAULT_ANIMATION_MS = 600;

/** Drives a ~600ms requestAnimationFrame loop from `start` to `target`,
 * calling `onFrame` with the eased intermediate pose each frame and
 * `onDone` once after the final frame. Returns a cancel function -- callers
 * that select a new preset mid-animation should cancel the previous run
 * first so two animations never fight over the same joints. */
export function runPoseAnimation(
  start: PoseValues,
  target: PoseValues,
  onFrame: (values: PoseValues) => void,
  onDone?: () => void,
  durationMs = DEFAULT_ANIMATION_MS,
): () => void {
  let cancelled = false;
  let handle: number | null = null;
  const startTime = performance.now();

  function tick(now: number) {
    if (cancelled) return;
    const t = Math.min(1, (now - startTime) / durationMs);
    onFrame(interpolatePose(start, target, t));
    if (t < 1) {
      handle = requestAnimationFrame(tick);
    } else {
      onDone?.();
    }
  }

  handle = requestAnimationFrame(tick);
  return () => {
    cancelled = true;
    if (handle !== null) cancelAnimationFrame(handle);
  };
}
