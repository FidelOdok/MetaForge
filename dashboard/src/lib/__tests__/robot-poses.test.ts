import { describe, expect, it } from 'vitest';
import {
  clampToJointLimits,
  zeroPose,
  homePose,
  minPose,
  maxPose,
  upsertNamedPose,
  easeInOutCubic,
  interpolatePose,
} from '../robot-poses';
import type { RobotJointInfo } from '../../store/viewer-store';

const JOINTS: RobotJointInfo[] = [
  { name: 'joint_1', lower: -1, upper: 1, initial: 0 },
  { name: 'joint_2', lower: 0, upper: 4, initial: 0 },
];

describe('clampToJointLimits', () => {
  it('clamps values above the upper limit', () => {
    expect(clampToJointLimits(JOINTS, { joint_1: 5, joint_2: 2 })).toEqual({
      joint_1: 1,
      joint_2: 2,
    });
  });

  it('clamps values below the lower limit', () => {
    expect(clampToJointLimits(JOINTS, { joint_1: -5, joint_2: -2 })).toEqual({
      joint_1: -1,
      joint_2: 0,
    });
  });

  it('passes through values already within limits', () => {
    expect(clampToJointLimits(JOINTS, { joint_1: 0.5, joint_2: 3 })).toEqual({
      joint_1: 0.5,
      joint_2: 3,
    });
  });

  it('falls back to the joint initial for a missing value', () => {
    expect(clampToJointLimits(JOINTS, {})).toEqual({ joint_1: 0, joint_2: 0 });
  });
});

describe('built-in presets', () => {
  it('zeroPose sets every joint to 0', () => {
    expect(zeroPose(JOINTS)).toEqual({ joint_1: 0, joint_2: 0 });
  });

  it('homePose is the midpoint of each joint range', () => {
    expect(homePose(JOINTS)).toEqual({ joint_1: 0, joint_2: 2 });
  });

  it('minPose sets every joint to its lower limit', () => {
    expect(minPose(JOINTS)).toEqual({ joint_1: -1, joint_2: 0 });
  });

  it('maxPose sets every joint to its upper limit', () => {
    expect(maxPose(JOINTS)).toEqual({ joint_1: 1, joint_2: 4 });
  });

  it('Home differs from Zero when a joint range is not centered on 0', () => {
    const offCenterJoints: RobotJointInfo[] = [{ name: 'joint_3', lower: 0, upper: 4, initial: 0 }];
    expect(homePose(offCenterJoints)).not.toEqual(zeroPose(offCenterJoints));
  });
});

describe('upsertNamedPose', () => {
  it('adds a pose to an empty map', () => {
    expect(upsertNamedPose(undefined, 'Extended', { joint_1: 1 })).toEqual({
      Extended: { joint_1: 1 },
    });
  });

  it('adds a pose without touching existing ones', () => {
    const existing = { Home: { joint_1: 0 } };
    expect(upsertNamedPose(existing, 'Extended', { joint_1: 1 })).toEqual({
      Home: { joint_1: 0 },
      Extended: { joint_1: 1 },
    });
  });

  it('overwrites a pose saved under the same name', () => {
    const existing = { Extended: { joint_1: 0.5 } };
    expect(upsertNamedPose(existing, 'Extended', { joint_1: 1 })).toEqual({
      Extended: { joint_1: 1 },
    });
  });
});

describe('easeInOutCubic', () => {
  it('starts at 0 and ends at 1', () => {
    expect(easeInOutCubic(0)).toBe(0);
    expect(easeInOutCubic(1)).toBe(1);
  });

  it('is exactly 0.5 at the midpoint (symmetric ease)', () => {
    expect(easeInOutCubic(0.5)).toBeCloseTo(0.5, 10);
  });

  it('clamps t outside [0, 1]', () => {
    expect(easeInOutCubic(-1)).toBe(0);
    expect(easeInOutCubic(2)).toBe(1);
  });
});

describe('interpolatePose', () => {
  it('is the start pose at t=0', () => {
    expect(interpolatePose({ joint_1: 0 }, { joint_1: 10 }, 0)).toEqual({ joint_1: 0 });
  });

  it('is the target pose at t=1', () => {
    expect(interpolatePose({ joint_1: 0 }, { joint_1: 10 }, 1)).toEqual({ joint_1: 10 });
  });

  it('is partway between start and target at t=0.5, eased', () => {
    const result = interpolatePose({ joint_1: 0 }, { joint_1: 10 }, 0.5);
    expect(result.joint_1).toBeCloseTo(5, 10);
  });

  it('snaps a joint missing from start straight to its target', () => {
    expect(interpolatePose({}, { joint_1: 3 }, 0)).toEqual({ joint_1: 3 });
  });
});
