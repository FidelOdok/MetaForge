import { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useThree, type ThreeEvent } from '@react-three/fiber';
import { Bounds, OrbitControls, useGLTF } from '@react-three/drei';
import * as THREE from 'three';
import { VIEWER_COLOR_MANAGEMENT, VIEWER_GPU_PREFERENCE } from '../viewer/viewerColor';
import { ErrorBoundary } from '../ErrorBoundary';
import { createAxisDrag, type AxisDrag } from '../../lib/preview/dragAxis';
import type { PreviewBuild, PreviewHandle } from '../../lib/preview/buildPreview';

/**
 * The 3D view of a parametric component (C3D slice 5): the L2 preview mesh,
 * the L3 tessellation of a validated exact revision (main after the swap,
 * faint underneath while the preview leads), and Strategy A drag handles.
 * Kept apart from the controls so they are testable without WebGL.
 */
export interface ParametricCanvasProps {
  build: PreviewBuild | null;
  show: 'l2' | 'l3';
  /** The L3 GLB, when a validated exact revision exists. */
  l3Url: string | null;
  /** Millimetres per unit of the edited values. */
  scaleToMm: number;
  onHandleChange: (handle: PreviewHandle, value: number) => void;
  onHandleRelease: (handle: PreviewHandle, value: number) => void;
  /** Handles are hidden while a commit is on its way. */
  handlesEnabled: boolean;
}

function L3Model({ url, ghost }: { url: string; ghost: boolean }) {
  const { scene } = useGLTF(url);
  const copy = useMemo(() => {
    const c = scene.clone(true);
    if (ghost) {
      c.traverse((o) => {
        const mesh = o as THREE.Mesh;
        if (mesh.isMesh) {
          mesh.material = new THREE.MeshStandardMaterial({ color: '#8a94a6', transparent: true, opacity: 0.22, depthWrite: false });
        }
      });
    }
    return c;
  }, [scene, ghost]);
  return <primitive object={copy} />;
}

function DragHandle({
  handle,
  scaleToMm,
  onChange,
  onRelease,
  setDragging,
}: {
  handle: PreviewHandle;
  scaleToMm: number;
  onChange: (v: number) => void;
  onRelease: (v: number) => void;
  setDragging: (on: boolean) => void;
}) {
  const { camera, size } = useThree();
  const drag = useRef<AxisDrag | null>(null);
  const [hover, setHover] = useState(false);

  const project = (p: THREE.Vector3): [number, number] => {
    const v = p.clone().project(camera);
    return [((v.x + 1) / 2) * size.width, ((1 - v.y) / 2) * size.height];
  };

  const onPointerDown = (e: ThreeEvent<PointerEvent>) => {
    e.stopPropagation();
    const at = new THREE.Vector3(...handle.position);
    const step = new THREE.Vector3(...handle.direction).multiplyScalar(1);
    const [x0, y0] = project(at);
    const [x1, y1] = project(at.clone().add(step));
    const pxPerMm = Math.hypot(x1 - x0, y1 - y0);
    const startX = e.nativeEvent.clientX;
    const startY = e.nativeEvent.clientY;
    drag.current = createAxisDrag({
      startValue: handle.value,
      pxPerUnit: pxPerMm * (scaleToMm || 1),
      screenDir: [x1 - x0, y1 - y0],
      onChange,
      onCommit: onRelease,
    });
    setDragging(true);
    const move = (ev: PointerEvent) => drag.current?.move(ev.clientX - startX, ev.clientY - startY);
    const end = () => {
      drag.current?.release();
      drag.current = null;
      setDragging(false);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
      window.removeEventListener('keydown', key);
    };
    const key = (ev: KeyboardEvent) => {
      if (ev.key !== 'Escape') return;
      drag.current?.cancel();
      end();
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    window.addEventListener('keydown', key);
  };

  const radius = 1.6;
  return (
    <mesh
      position={handle.position}
      onPointerDown={onPointerDown}
      onPointerOver={() => setHover(true)}
      onPointerOut={() => setHover(false)}
    >
      <sphereGeometry args={[radius, 16, 12]} />
      <meshBasicMaterial color={hover ? '#ffb020' : '#ff7a00'} depthTest={false} transparent opacity={0.95} />
    </mesh>
  );
}

export function ParametricCanvas({ build, show, l3Url, scaleToMm, onHandleChange, onHandleRelease, handlesEnabled }: ParametricCanvasProps) {
  const [dragging, setDragging] = useState(false);
  const material = useMemo(
    () => new THREE.MeshStandardMaterial({ color: '#c9a86a', roughness: 0.6, metalness: 0.05, side: THREE.DoubleSide }),
    [],
  );
  useEffect(() => () => material.dispose(), [material]);
  return (
    <ErrorBoundary fallback={<div className="cv-stage-empty">The 3D view could not start.</div>}>
      <Canvas
        camera={{ position: [80, -90, 70], up: [0, 0, 1], fov: 45, near: 0.1, far: 100000 }}
        gl={{ ...VIEWER_GPU_PREFERENCE, ...VIEWER_COLOR_MANAGEMENT }}
        style={{ width: '100%', height: '100%' }}
      >
        <ambientLight intensity={0.6} />
        <directionalLight position={[100, -160, 120]} intensity={1.5} />
        <directionalLight position={[-120, 40, -80]} intensity={0.45} />
        <Bounds fit clip margin={1.3}>
          {show === 'l2' && build && <mesh geometry={build.geometry} material={material} />}
          {l3Url && (
            <Suspense fallback={null}>
              <L3Model url={l3Url} ghost={show === 'l2'} />
            </Suspense>
          )}
        </Bounds>
        {handlesEnabled &&
          build?.handles.map((h) => (
            <DragHandle
              key={`${h.kind}:${h.constraintId ?? h.parameter}`}
              handle={h}
              scaleToMm={scaleToMm}
              onChange={(v) => onHandleChange(h, v)}
              onRelease={(v) => onHandleRelease(h, v)}
              setDragging={setDragging}
            />
          ))}
        <OrbitControls makeDefault enabled={!dragging} />
      </Canvas>
    </ErrorBoundary>
  );
}
