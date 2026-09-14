import { useEffect, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import type { URDFRobot } from 'urdf-loader';
import RAPIER from '@dimforge/rapier3d-compat';

/**
 * MET-737 (explicit follow-up ask, beyond the original scoping): a
 * lightweight, best-effort physics mode for the URDF preview, on top of
 * `urdf-loader`'s kinematic `setJointValue` posing.
 *
 * `@dimforge/rapier3d-compat` was already present in the dashboard's
 * dependency tree as a transitive dep (of `@types/three`, unused anywhere
 * in `dashboard/src` before this) — now a direct dependency. `@react-three/
 * rapier` (the idiomatic R3F wrapper) was deliberately NOT used: it requires
 * `@react-three/fiber@^9` + `react@^19`, and this dashboard is on
 * `@react-three/fiber@8`/`react@18` — bumping those majors is a much bigger,
 * separate change than "add a physics toggle to one preview panel". This
 * hook drives the raw Rapier API directly from a `useFrame` step instead.
 *
 * Scope, stated plainly rather than overclaimed:
 * - Colliders are axis-aligned bounding boxes per link (from the loaded
 *   mesh geometry), not full triangle meshes -- a deliberate "lightweight"
 *   simplification, not a precision physics engine.
 * - The URDF's own kinematic tree (`joint.position`/`joint.axis`/
 *   `child.position`, all already in meters -- URDF units, unlike this
 *   session's mm-based CAD/export code) is read ONCE at physics-enable time
 *   to build matching Rapier rigid bodies + joints at the robot's rest pose.
 *   It is not re-synced against manual slider posing while physics runs --
 *   the two modes are mutually exclusive by design (see the panel's
 *   toggle), not layered.
 * - A joint/link that fails to build a physics counterpart is skipped with
 *   a console warning rather than crashing the whole preview -- the same
 *   "degrade honestly, don't silently fake it" rule used throughout this
 *   session's backend work.
 * - The base/root link is fixed (welded to the world origin); everything
 *   else is dynamic and falls under gravity, constrained by the joints.
 */

let rapierInitPromise: Promise<void> | null = null;
function ensureRapierInit(): Promise<void> {
  if (!rapierInitPromise) {
    rapierInitPromise = RAPIER.init();
  }
  return rapierInitPromise;
}

const _box = new THREE.Box3();
const _size = new THREE.Vector3();
const _center = new THREE.Vector3();
const _quat = new THREE.Quaternion();

function linkBoundsLocal(link: THREE.Object3D): { halfExtents: THREE.Vector3; center: THREE.Vector3 } | null {
  _box.makeEmpty();
  link.traverse((child) => {
    const mesh = child as THREE.Mesh;
    if (mesh.isMesh && mesh.geometry) {
      mesh.geometry.computeBoundingBox?.();
      const bb = mesh.geometry.boundingBox;
      if (bb) {
        const localBox = bb.clone().applyMatrix4(mesh.matrix);
        _box.union(localBox);
      }
    }
  });
  if (_box.isEmpty()) return null;
  _box.getSize(_size);
  _box.getCenter(_center);
  // Rapier cuboids need non-zero half-extents; clamp a degenerate (flat)
  // dimension to a thin slab rather than rejecting the collider outright.
  const MIN_HALF_EXTENT = 1e-4;
  return {
    halfExtents: new THREE.Vector3(
      Math.max(_size.x / 2, MIN_HALF_EXTENT),
      Math.max(_size.y / 2, MIN_HALF_EXTENT),
      Math.max(_size.z / 2, MIN_HALF_EXTENT),
    ),
    center: _center.clone(),
  };
}

interface PhysicsState {
  world: RAPIER.World;
  bodies: Map<string, RAPIER.RigidBody>;
  linkObjects: Map<string, THREE.Object3D>;
}

export function useUrdfPhysics(robot: URDFRobot | null, enabled: boolean) {
  const stateRef = useRef<PhysicsState | null>(null);
  const readyRef = useRef(false);

  useEffect(() => {
    readyRef.current = false;
    stateRef.current = null;
    if (!robot || !enabled) return;

    let cancelled = false;
    ensureRapierInit().then(() => {
      if (cancelled) return;

      robot.updateMatrixWorld(true);
      const world = new RAPIER.World({ x: 0, y: -9.81, z: 0 });
      const bodies = new Map<string, RAPIER.RigidBody>();
      const linkObjects = new Map<string, THREE.Object3D>();

      // Root link is fixed (welded in place); every other link is dynamic
      // and gets pulled into the correct pose by the joint constraints
      // below, starting from the URDF's own rest pose.
      for (const [name, link] of Object.entries(robot.links)) {
        const bounds = linkBoundsLocal(link);
        if (!bounds) {
          console.warn(`[UrdfPreview] link "${name}" has no mesh geometry — skipping its collider`);
          continue;
        }
        link.getWorldPosition(_center);
        link.getWorldQuaternion(_quat);
        const isRoot = link === robot;
        const bodyDesc = (isRoot ? RAPIER.RigidBodyDesc.fixed() : RAPIER.RigidBodyDesc.dynamic())
          .setTranslation(_center.x, _center.y, _center.z)
          .setRotation({ x: _quat.x, y: _quat.y, z: _quat.z, w: _quat.w });
        const body = world.createRigidBody(bodyDesc);
        const colliderDesc = RAPIER.ColliderDesc.cuboid(
          bounds.halfExtents.x,
          bounds.halfExtents.y,
          bounds.halfExtents.z,
        ).setTranslation(bounds.center.x, bounds.center.y, bounds.center.z);
        world.createCollider(colliderDesc, body);
        bodies.set(name, body);
        linkObjects.set(name, link);
      }

      for (const [name, joint] of Object.entries(robot.joints)) {
        try {
          const parentLink = joint.parent as THREE.Object3D | null;
          const childLink = joint.children[0] as THREE.Object3D | undefined;
          const parentName = parentLink ? Object.keys(robot.links).find((k) => robot.links[k] === parentLink) : undefined;
          const childName = childLink ? Object.keys(robot.links).find((k) => robot.links[k] === childLink) : undefined;
          if (!parentName || !childName) continue;
          const bodyA = bodies.get(parentName);
          const bodyB = bodies.get(childName);
          if (!bodyA || !bodyB) continue;

          const anchor1 = { x: joint.position.x, y: joint.position.y, z: joint.position.z };
          const anchor2 = { x: childLink!.position.x, y: childLink!.position.y, z: childLink!.position.z };
          const axis = { x: joint.axis.x, y: joint.axis.y, z: joint.axis.z };

          let jointData: RAPIER.JointData | null = null;
          if (joint.jointType === 'fixed') {
            jointData = RAPIER.JointData.fixed(
              anchor1,
              { x: 0, y: 0, z: 0, w: 1 },
              anchor2,
              { x: 0, y: 0, z: 0, w: 1 },
            );
          } else if (joint.jointType === 'continuous' || joint.jointType === 'revolute') {
            const revolute = RAPIER.JointData.revolute(anchor1, anchor2, axis);
            if (joint.jointType === 'revolute' && Number.isFinite(joint.limit.lower) && Number.isFinite(joint.limit.upper)) {
              revolute.limitsEnabled = true;
              revolute.limits = [joint.limit.lower, joint.limit.upper];
            }
            jointData = revolute;
          } else if (joint.jointType === 'prismatic') {
            const prismatic = RAPIER.JointData.prismatic(anchor1, anchor2, axis);
            prismatic.limitsEnabled = true;
            prismatic.limits = [joint.limit.lower, joint.limit.upper];
            jointData = prismatic;
          }
          // planar/floating: no direct Rapier equivalent wired up here —
          // URDF export never produces these (see _URDF_JOINT_TYPE_MAP in
          // operations.py), so skipping them isn't a real-world gap.

          if (jointData) {
            world.createImpulseJoint(jointData, bodyA, bodyB, true);
          }
        } catch (err) {
          console.warn(`[UrdfPreview] failed to build a physics joint for "${name}" — skipping`, err);
        }
      }

      stateRef.current = { world, bodies, linkObjects };
      readyRef.current = true;
    });

    return () => {
      cancelled = true;
      stateRef.current?.world.free();
      stateRef.current = null;
      readyRef.current = false;
    };
  }, [robot, enabled]);

  useFrame(() => {
    if (!enabled || !readyRef.current || !stateRef.current) return;
    const { world, bodies, linkObjects } = stateRef.current;
    world.step();

    for (const [name, body] of bodies) {
      const link = linkObjects.get(name);
      if (!link || !link.parent) continue;
      const t = body.translation();
      const r = body.rotation();
      // Bodies were seeded from WORLD transforms; write the result back the
      // same way, then convert into the link's own parent-local space so
      // three.js's normal (nested) rendering continues to work unmodified.
      const worldPos = new THREE.Vector3(t.x, t.y, t.z);
      const worldQuat = new THREE.Quaternion(r.x, r.y, r.z, r.w);
      link.parent.updateMatrixWorld(true);
      const parentInverse = new THREE.Matrix4().copy(link.parent.matrixWorld).invert();
      const localMatrix = new THREE.Matrix4()
        .compose(worldPos, worldQuat, new THREE.Vector3(1, 1, 1))
        .premultiply(parentInverse);
      localMatrix.decompose(link.position, link.quaternion, link.scale);
    }
  });
}
