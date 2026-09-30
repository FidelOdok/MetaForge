import { PointerURDFDragControls } from 'urdf-loader/src/URDFDragControls.js';
import type { Camera, Material, Mesh, Object3D } from 'three';
import { Color } from 'three';
import type { URDFJoint, URDFRobot } from 'urdf-loader';

/** urdf-loader assigns `MeshPhongMaterial` by default (see URDFLoader.js);
 * this codebase doesn't depend on which lit material a link ends up with,
 * only that it exposes the `emissive`/`emissiveIntensity` pair every
 * three.js lit material (Phong, Standard, Physical, ...) shares. */
interface EmissiveMaterial extends Material {
  emissive?: Color;
  emissiveIntensity?: number;
}

const HOVER_EMISSIVE = new Color('#ff5a0a'); // Kinetic Console accent (project_kinetic_console_design)
const HOVER_EMISSIVE_INTENSITY = 0.5;

/** Meshes belonging to the single link a joint moves -- traverses from the
 * joint's child link but stops at the next URDFJoint boundary, so hovering
 * joint_2 highlights only link_2's own geometry, not the whole downstream
 * arm hanging off it. */
export function meshesOfJointChild(joint: URDFJoint): Mesh[] {
  const meshes: Mesh[] = [];
  function walk(obj: Object3D) {
    const asMesh = obj as Mesh;
    if (asMesh.isMesh) meshes.push(asMesh);
    for (const child of obj.children) {
      if ((child as URDFJoint).isURDFJoint) continue;
      walk(child);
    }
  }
  // Start from `joint` itself (never a Mesh, so nothing extra gets pushed
  // for it) rather than looping its children directly -- applies the same
  // isURDFJoint skip check uniformly at every depth, including a child
  // joint that sits immediately under this one (a real bug caught by the
  // "stops at the next URDFJoint" test: looping joint.children directly
  // only checked the skip for grandchildren, never for a direct child that
  // was itself a joint).
  walk(joint as unknown as Object3D);
  return meshes;
}

/** urdf-loader's `PointerURDFDragControls` already does the raycast, joint-
 * type dispatch (revolute/continuous rotate about the joint axis, prismatic
 * slides along it) and URDF-limit clamping (via the underlying
 * `URDFJoint.setJointValue`, see FORGE-250's Jira comment for the exact
 * clamp confirmation) -- this subclass only adds the three things FORGE-250
 * needs on top: reporting the resulting joint value back to the viewer
 * store (so the slider stays in sync), a link-mesh hover highlight, and
 * drag-start/end callbacks (so the caller can suspend orbit controls). */
export class RobotPoseDragControls extends PointerURDFDragControls {
  private readonly onJointChange: (name: string, value: number) => void;
  private readonly onHoverChange: (jointName: string | null) => void;
  private readonly onDragStateChange: (dragging: boolean) => void;
  private readonly originalMaterials = new Map<Mesh, Material | Material[]>();

  constructor(
    robot: URDFRobot,
    camera: Camera,
    domElement: HTMLElement,
    callbacks: {
      onJointChange: (name: string, value: number) => void;
      onHoverChange: (jointName: string | null) => void;
      onDragStateChange: (dragging: boolean) => void;
    },
  ) {
    // `robot` (not the full scene) as the raycast root -- confines hits to
    // this robot's own meshes, so ground plane / gizmo / other viewer
    // objects sharing the Canvas can never be grabbed as a "joint".
    super(robot, camera, domElement);
    this.onJointChange = callbacks.onJointChange;
    this.onHoverChange = callbacks.onHoverChange;
    this.onDragStateChange = callbacks.onDragStateChange;
  }

  override updateJoint(joint: URDFJoint, angle: number): void {
    super.updateJoint(joint, angle);
    // Read back `joint.angle` rather than echoing `angle` -- setJointValue
    // clamps revolute/prismatic joints to their URDF limits internally, so
    // the post-clamp value (not the raw drag delta) is what the slider and
    // any persisted pose must reflect.
    this.onJointChange(joint.name, joint.angle);
  }

  override onHover(joint: URDFJoint): void {
    this.onHoverChange(joint.name);
    this.setLinkHighlight(joint, true);
  }

  override onUnhover(joint: URDFJoint): void {
    this.onHoverChange(null);
    this.setLinkHighlight(joint, false);
  }

  override onDragStart(joint: URDFJoint): void {
    this.onDragStateChange(true);
    this.setLinkHighlight(joint, true);
  }

  override onDragEnd(): void {
    this.onDragStateChange(false);
  }

  private setLinkHighlight(joint: URDFJoint, highlighted: boolean): void {
    for (const mesh of meshesOfJointChild(joint)) {
      if (highlighted) {
        if (!this.originalMaterials.has(mesh)) {
          this.originalMaterials.set(mesh, mesh.material);
          const clones = Array.isArray(mesh.material)
            ? mesh.material.map((m) => m.clone())
            : mesh.material.clone();
          mesh.material = clones;
        }
        const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
        for (const m of materials as EmissiveMaterial[]) {
          if (m.emissive) {
            m.emissive.copy(HOVER_EMISSIVE);
            m.emissiveIntensity = HOVER_EMISSIVE_INTENSITY;
          }
        }
      } else {
        const original = this.originalMaterials.get(mesh);
        if (original) {
          mesh.material = original;
          this.originalMaterials.delete(mesh);
        }
      }
    }
  }

  /** Restores every currently-highlighted mesh's original material before
   * disposing the base class's DOM listeners -- otherwise a link left
   * hovered at unmount (e.g. physics mode toggled on mid-hover) would stay
   * tinted forever. */
  override dispose(): void {
    for (const [mesh, original] of this.originalMaterials) {
      mesh.material = original;
    }
    this.originalMaterials.clear();
    super.dispose();
  }
}
