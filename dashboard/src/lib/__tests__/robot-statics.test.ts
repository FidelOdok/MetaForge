import { describe, expect, it } from 'vitest';
import { Object3D } from 'three';
import type { URDFJoint, URDFLink, URDFRobot } from 'urdf-loader';
import { buildJointLoadChainPayload } from '../robot-statics';

/**
 * Builds a minimal fake URDFRobot-shaped scene graph -- real three.js
 * Object3D instances (so matrixWorld/localToWorld behave exactly as they
 * would for a real urdf-loader parse), with just the `isURDFLink`/
 * `inertial` fields buildJointLoadChainPayload actually reads. Not testing
 * urdf-loader's own XML parsing (out of scope here) -- only that this
 * function reads world transforms and inertial data correctly once a robot
 * has already been posed.
 */
function makeFakeChain(): URDFRobot {
  const robot = new Object3D();

  const joint0 = new Object3D() as unknown as URDFJoint;
  joint0.name = 'joint0';
  joint0.position.set(0, 0, 0);

  const link0 = new Object3D() as unknown as URDFLink;
  link0.name = 'link0';
  (link0 as unknown as { isURDFLink: boolean }).isURDFLink = true;
  link0.inertial = { mass: 2.0, origin: { xyz: [0.1, 0, 0], rpy: [0, 0, 0] }, inertia: {} as never };

  (joint0 as unknown as Object3D).add(link0 as unknown as Object3D);
  robot.add(joint0 as unknown as Object3D);

  const fakeRobot = robot as unknown as URDFRobot;
  fakeRobot.joints = { joint0 };
  fakeRobot.links = { link0 };
  return fakeRobot;
}

describe('buildJointLoadChainPayload', () => {
  it('reads joint world position and link CoM, converting metres to millimetres', () => {
    const robot = makeFakeChain();

    const payload = buildJointLoadChainPayload(robot, ['joint0']);

    expect(payload.joints).toEqual([{ name: 'joint0', position_world_mm: [0, 0, 0] }]);
    expect(payload.links).toEqual([
      { name: 'link0', com_world_mm: [100, 0, 0], mass_kg: 2.0 },
    ]);
  });

  it('offsets a second joint by the chain position', () => {
    const robot = makeFakeChain();
    const joint1 = new Object3D() as unknown as URDFJoint;
    joint1.name = 'joint1';
    joint1.position.set(0.2, 0, 0); // 200mm out along the chain (child of joint0's link)

    const link1 = new Object3D() as unknown as URDFLink;
    link1.name = 'link1';
    (link1 as unknown as { isURDFLink: boolean }).isURDFLink = true;
    link1.inertial = { mass: 1.0, origin: { xyz: [0.05, 0, 0], rpy: [0, 0, 0] }, inertia: {} as never };

    (joint1 as unknown as Object3D).add(link1 as unknown as Object3D);
    (robot.links['link0'] as unknown as Object3D).add(joint1 as unknown as Object3D);
    robot.joints['joint1'] = joint1;
    robot.links['link1'] = link1;

    const payload = buildJointLoadChainPayload(robot, ['joint0', 'joint1']);

    expect(payload.joints).toHaveLength(2);
    expect(payload.links).toHaveLength(2);
    const secondJoint = payload.joints[1];
    const secondLink = payload.links[1];
    if (!secondJoint || !secondLink) throw new Error('expected two joints/links');

    // link0's own frame origin coincides with joint0's (its inertial offset
    // only affects its CoM, not its frame), so joint1 -- a child of link0,
    // offset 200mm in link0's frame -- lands at world x=200mm.
    expect(secondJoint.position_world_mm[0]).toBeCloseTo(200, 6);
    // link1 is a child of joint1 with no frame offset of its own, so its
    // world CoM is joint1's position plus link1's own 50mm inertial offset.
    expect(secondLink.com_world_mm[0]).toBeCloseTo(250, 6);
  });

  it('skips a joint name not present on the robot', () => {
    const robot = makeFakeChain();
    const payload = buildJointLoadChainPayload(robot, ['joint0', 'nonexistent']);
    expect(payload.joints).toHaveLength(1);
  });
});
