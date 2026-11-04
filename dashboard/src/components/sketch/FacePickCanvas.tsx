import { Suspense, useEffect, useMemo } from 'react';
import { Canvas, type ThreeEvent } from '@react-three/fiber';
import { OrbitControls, useGLTF } from '@react-three/drei';
import * as THREE from 'three';
import type { CandidateFace } from '../../api/endpoints/sketchPlanes';
import { faceTriangles, highlightIndices, pickFace, planeTolerance, type MeshLike } from '../../lib/sketch/faceMesh';
import { VIEWER_GPU_PREFERENCE } from '../viewer/viewerColor';

const HOVER_COLOR = '#ff5a0a';
const CANDIDATE_COLOR = '#3b82f6';

export interface FacePickCanvasProps {
  glbUrl: string;
  faces: CandidateFace[];
  partSize: number;
  hovered: string | null;
  /** Faces to show as candidates (a remap that could not choose). */
  highlighted: string[];
  onHover: (faceId: string | null) => void;
  onPick: (faceId: string) => void;
}

function meshLike(geometry: THREE.BufferGeometry): MeshLike {
  return {
    positions: geometry.getAttribute('position').array as ArrayLike<number>,
    index: geometry.getIndex()?.array ?? null,
  };
}

function Overlay({ mesh, geometry, face, color, tol }: { mesh: MeshLike; geometry: THREE.BufferGeometry; face: CandidateFace; color: string; tol: number }) {
  const overlay = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', geometry.getAttribute('position'));
    g.setIndex(highlightIndices(mesh, faceTriangles(mesh, face, tol)));
    return g;
  }, [mesh, geometry, face, tol]);
  useEffect(() => () => overlay.dispose(), [overlay]);
  return (
    <mesh geometry={overlay} renderOrder={2}>
      <meshBasicMaterial color={color} transparent opacity={0.55} side={THREE.DoubleSide} depthWrite={false} polygonOffset polygonOffsetFactor={-2} />
    </mesh>
  );
}

function PickableModel({ glbUrl, faces, partSize, hovered, highlighted, onHover, onPick }: FacePickCanvasProps) {
  const { scene } = useGLTF(glbUrl);
  // Parts in the converter's order: part_index n is the n-th mesh.
  const parts = useMemo(() => {
    const out: THREE.Mesh[] = [];
    scene.updateMatrixWorld(true);
    scene.traverse((o) => {
      if ((o as THREE.Mesh).isMesh) out.push(o as THREE.Mesh);
    });
    return out.map((m, i) => ({
      mesh: m,
      like: meshLike(m.geometry as THREE.BufferGeometry),
      faces: faces.filter((f) => (f.mesh?.part_index ?? 0) === i),
    }));
  }, [scene, faces]);
  const tol = planeTolerance(partSize);
  const marked = new Set([...(hovered ? [hovered] : []), ...highlighted]);

  return (
    <group>
      {parts.map((p, i) => (
        <group key={i} matrixAutoUpdate={false} matrix={p.mesh.matrixWorld}>
          <mesh
            geometry={p.mesh.geometry}
            onPointerMove={(e: ThreeEvent<PointerEvent>) => {
              e.stopPropagation();
              const face = e.faceIndex != null ? pickFace(p.like, p.faces, e.faceIndex, tol) : null;
              onHover(face?.face_id ?? null);
            }}
            onPointerOut={() => onHover(null)}
            onClick={(e: ThreeEvent<MouseEvent>) => {
              e.stopPropagation();
              const face = e.faceIndex != null ? pickFace(p.like, p.faces, e.faceIndex, tol) : null;
              if (face) onPick(face.face_id);
            }}
          >
            <meshStandardMaterial color="#9ca3af" metalness={0} roughness={0.8} />
          </mesh>
          {p.faces
            .filter((f) => marked.has(f.face_id))
            .map((f) => (
              <Overlay
                key={f.face_id}
                mesh={p.like}
                geometry={p.mesh.geometry as THREE.BufferGeometry}
                face={f}
                tol={tol}
                color={f.face_id === hovered ? HOVER_COLOR : CANDIDATE_COLOR}
              />
            ))}
        </group>
      ))}
    </group>
  );
}

/**
 * The 3D face picker: the revision's converted GLB, planar faces lit up
 * under the pointer through the face-to-triangle map, a click picks.
 * Kept in its own module so the canvas loads only when it is opened.
 */
export default function FacePickCanvas(props: FacePickCanvasProps) {
  const r = Math.max(props.partSize, 1);
  const centre = useMemo<[number, number, number]>(() => {
    if (props.faces.length === 0) return [0, 0, 0];
    const sum: [number, number, number] = [0, 0, 0];
    for (const f of props.faces) {
      sum[0] += f.centroid[0];
      sum[1] += f.centroid[1];
      sum[2] += f.centroid[2];
    }
    const k = props.faces.length;
    return [sum[0] / k, sum[1] / k, sum[2] / k];
  }, [props.faces]);
  return (
    <Canvas
      className="sk-face-canvas"
      gl={{ ...VIEWER_GPU_PREFERENCE }}
      camera={{ position: [centre[0] + r, centre[1] - r, centre[2] + r], up: [0, 0, 1], near: r / 1000, far: r * 100 }}
    >
      <ambientLight intensity={0.7} />
      <directionalLight position={[r, -r, 2 * r]} intensity={0.8} />
      <Suspense fallback={null}>
        <PickableModel {...props} />
      </Suspense>
      <OrbitControls makeDefault target={centre} />
    </Canvas>
  );
}
