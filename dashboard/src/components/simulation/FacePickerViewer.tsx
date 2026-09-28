import { useMemo } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import * as THREE from 'three';
import type { NamedFace } from '../../types/loadCase';

interface FacePickerViewerProps {
  faces: NamedFace[];
  fixedFace: string;
  loadFace: string;
  /** Which of the two selections a face click sets. */
  pickMode: 'fixed' | 'load';
  onPickFixed: (name: string) => void;
  onPickLoad: (name: string) => void;
}

const FIXED_COLOR = '#3b82f6'; // blue — mirrors a support symbol, not the brand accent
const LOAD_COLOR = '#ff5a0a'; // Kinetic Console primary-container orange
const IDLE_COLOR = '#71717a';

const MIN_PATCH_SIDE_MM = 2;
const MAX_PATCH_SIDE_MM = 500;

/** A square patch of roughly the face's real footprint (`sqrt(area)`), not
 * its exact tessellated outline — FORGE-277 deliberately doesn't round-trip
 * the mesh's real triangle geometry into the viewer, only its per-face
 * summary (centroid/normal/area/bbox), so a patch is an honest, cheap
 * stand-in rather than the actual face shape. */
function facePatchSide(areaMm2: number): number {
  const side = Math.sqrt(Math.max(areaMm2, 0));
  return Math.min(Math.max(side, MIN_PATCH_SIDE_MM), MAX_PATCH_SIDE_MM);
}

function normalQuaternion(normal: [number, number, number]): THREE.Quaternion {
  const from = new THREE.Vector3(0, 0, 1);
  const to = new THREE.Vector3(...normal);
  if (to.lengthSq() < 1e-9) return new THREE.Quaternion();
  to.normalize();
  return new THREE.Quaternion().setFromUnitVectors(from, to);
}

function FacePatch({
  face,
  color,
  opacity,
  onClick,
}: {
  face: NamedFace;
  color: string;
  opacity: number;
  onClick: () => void;
}) {
  const quaternion = useMemo(() => normalQuaternion(face.normal), [face.normal]);
  const side = facePatchSide(face.areaMm2);

  return (
    <mesh
      position={face.centroidMm}
      quaternion={quaternion}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      <planeGeometry args={[side, side]} />
      <meshStandardMaterial
        color={color}
        opacity={opacity}
        transparent
        side={THREE.DoubleSide}
        depthWrite={false}
      />
    </mesh>
  );
}

/** The load face's direction arrow — drawn along its outward normal so the
 * user can see, not just read, which way the force is being defined
 * relative to (an inward flip is a real modeling error this makes visible). */
function LoadArrow({ face }: { face: NamedFace }) {
  const arrow = useMemo(() => {
    const dir = new THREE.Vector3(...face.normal);
    if (dir.lengthSq() < 1e-9) dir.set(0, 0, 1);
    dir.normalize();
    const origin = new THREE.Vector3(...face.centroidMm);
    const length = Math.max(facePatchSide(face.areaMm2) * 0.9, 8);
    return new THREE.ArrowHelper(dir, origin, length, 0xff5a0a, length * 0.35, length * 0.25);
  }, [face]);

  return <primitive object={arrow} />;
}

function sceneBounds(faces: NamedFace[]): { center: [number, number, number]; radius: number } {
  if (faces.length === 0) return { center: [0, 0, 0], radius: 50 };
  let minX = Infinity;
  let minY = Infinity;
  let minZ = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  let maxZ = -Infinity;
  for (const f of faces) {
    for (const [x, y, z] of [f.bboxMm.min, f.bboxMm.max]) {
      minX = Math.min(minX, x);
      minY = Math.min(minY, y);
      minZ = Math.min(minZ, z);
      maxX = Math.max(maxX, x);
      maxY = Math.max(maxY, y);
      maxZ = Math.max(maxZ, z);
    }
  }
  const min: [number, number, number] = [minX, minY, minZ];
  const max: [number, number, number] = [maxX, maxY, maxZ];
  const center: [number, number, number] = [
    (min[0] + max[0]) / 2,
    (min[1] + max[1]) / 2,
    (min[2] + max[2]) / 2,
  ];
  const radius = Math.max(
    Math.hypot(max[0] - min[0], max[1] - min[1], max[2] - min[2]) / 2,
    10,
  );
  return { center, radius };
}

/**
 * FORGE-277: renders a generated mesh's named faces as clickable patches in
 * 3D — real centroid/normal/area from ``freecad.generate_mesh``'s own face
 * table (FORGE-239), not the mesh's actual tessellated geometry (that would
 * need a STEP→GLB round trip this ticket deliberately doesn't take on; see
 * PR description). Clicking a patch sets it as the fixed or load face
 * (whichever ``pickMode`` is active) instead of typing an opaque gmsh group
 * name into a text field.
 */
export function FacePickerViewer({
  faces,
  fixedFace,
  loadFace,
  pickMode,
  onPickFixed,
  onPickLoad,
}: FacePickerViewerProps) {
  const { center, radius } = useMemo(() => sceneBounds(faces), [faces]);
  const cameraDistance = radius * 2.4;

  if (faces.length === 0) return null;

  return (
    <div
      data-testid="face-picker-viewer"
      style={{
        height: 240,
        borderRadius: 8,
        overflow: 'hidden',
        background: '#18181b',
        border: '1px solid rgba(65,72,90,0.3)',
      }}
    >
      <Canvas
        camera={{
          position: [
            center[0] + cameraDistance * 0.7,
            center[1] + cameraDistance * 0.55,
            center[2] + cameraDistance * 0.7,
          ],
          fov: 45,
          near: 0.1,
          far: 10000,
        }}
      >
        <ambientLight intensity={0.6} />
        <directionalLight position={[center[0] + radius * 2, center[1] + radius * 3, center[2] + radius]} intensity={0.8} />
        <OrbitControls makeDefault target={center} />
        {faces.map((face) => {
          const isFixed = face.name === fixedFace;
          const isLoad = face.name === loadFace;
          const color = isFixed ? FIXED_COLOR : isLoad ? LOAD_COLOR : IDLE_COLOR;
          const opacity = isFixed || isLoad ? 0.85 : 0.3;
          return (
            <FacePatch
              key={face.name}
              face={face}
              color={color}
              opacity={opacity}
              onClick={() => (pickMode === 'fixed' ? onPickFixed(face.name) : onPickLoad(face.name))}
            />
          );
        })}
        {loadFace &&
          (() => {
            const face = faces.find((f) => f.name === loadFace);
            return face ? <LoadArrow face={face} /> : null;
          })()}
      </Canvas>
    </div>
  );
}
