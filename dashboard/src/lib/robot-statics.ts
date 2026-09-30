import { Vector3 } from 'three';
import type { URDFLink, URDFRobot } from 'urdf-loader';

/**
 * Builds the posed-chain payload for POST /v1/robot/joint-loads (FORGE-283)
 * straight from an already-posed `URDFRobot` -- urdf-loader parses each
 * link's `<inertial>` block into `link.inertial.{mass, origin.xyz}` (local
 * frame, metres), and after `robot.setJointValue(...)` +
 * `robot.updateMatrixWorld(true)` every link/joint's `matrixWorld` already
 * reflects the current slider pose. So this does no forward kinematics of
 * its own -- it only reads world transforms Three.js has already resolved
 * and converts metres to millimetres (this repo's CalculiX mm+N+MPa
 * consistent unit system -- see deck_builder.py's module docstring).
 *
 * `jointOrder` must be base-to-tip (this codebase's own URDF generator
 * always emits joints in that order -- see operations.py's
 * `_build_assembly_urdf` -- and urdf-loader's `robot.joints` preserves
 * parse order via normal JS object key insertion). Only a single serial
 * chain is supported: each joint's child link (found via
 * `joint.children.find(c => c.isURDFLink)`) becomes that joint's paired
 * link, matching the backend's `links[i]`/`joints[i]` pairing convention
 * (`tool_registry/tools/calculix/statics.py`). A branching robot (e.g. a
 * multi-fingered gripper) isn't handled -- out of scope for this first
 * vertical slice, which targets the single-arm yardstick project.
 */
export interface JointLoadChainPayload {
  links: { name: string; com_world_mm: [number, number, number]; mass_kg: number }[];
  joints: { name: string; position_world_mm: [number, number, number] }[];
}

const M_TO_MM = 1000;

export function buildJointLoadChainPayload(
  robot: URDFRobot,
  jointOrder: string[]
): JointLoadChainPayload {
  robot.updateMatrixWorld(true);

  const links: JointLoadChainPayload['links'] = [];
  const joints: JointLoadChainPayload['joints'] = [];

  for (const jointName of jointOrder) {
    const joint = robot.joints[jointName];
    if (!joint) continue;

    const childLink = joint.children.find(
      (c): c is URDFLink => (c as URDFLink).isURDFLink === true
    );
    if (!childLink) continue;

    const jointWorldPos = new Vector3();
    joint.getWorldPosition(jointWorldPos);

    const inertial = childLink.inertial;
    const comLocal = new Vector3(...(inertial?.origin?.xyz ?? [0, 0, 0]));
    const comWorld = childLink.localToWorld(comLocal);

    joints.push({
      name: jointName,
      position_world_mm: [
        jointWorldPos.x * M_TO_MM,
        jointWorldPos.y * M_TO_MM,
        jointWorldPos.z * M_TO_MM,
      ],
    });
    links.push({
      name: childLink.name,
      com_world_mm: [comWorld.x * M_TO_MM, comWorld.y * M_TO_MM, comWorld.z * M_TO_MM],
      mass_kg: inertial?.mass ?? 0,
    });
  }

  return { links, joints };
}
