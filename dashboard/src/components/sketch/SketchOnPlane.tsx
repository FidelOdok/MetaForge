import { useEffect, useMemo } from 'react';
import { useThree } from '@react-three/fiber';
import * as THREE from 'three';
import { isPlaced, type SketchPlane } from '../../api/endpoints/sketchPlanes';
import { useSketchPlaneUi } from '../../hooks/use-sketch-plane';
import { useViewerStore } from '../../store/viewer-store';
import type { TinkercadControls } from '../../lib/TinkercadControls';
import {
  cadToViewer,
  displayFrame,
  displayScale,
  placeStrokes,
  viewHintFromDirection,
  viewerToCad,
  type PlaneFrame,
  type Vec2,
  type Vec3,
} from '../../lib/sketch/planeMath';

export interface SketchOnPlaneProps {
  plane: SketchPlane | null | undefined;
  strokes: readonly { id: string; points: readonly Vec2[] }[];
}

const STROKE_COLOR = '#ff5a0a';
const PREVIEW_COLOR = '#38bdf8';

export function frameOf(plane: SketchPlane): PlaneFrame {
  return { origin: plane.origin, u: plane.u_axis, v: plane.v_axis, normal: plane.normal };
}

function polyline(points: Vec3[], color: string, dashed = false): THREE.Line {
  const geometry = new THREE.BufferGeometry().setFromPoints(points.map((p) => new THREE.Vector3(p[0], p[1], p[2])));
  const material = dashed
    ? new THREE.LineDashedMaterial({ color, dashSize: 2, gapSize: 1.5, depthTest: false, transparent: true })
    : new THREE.LineBasicMaterial({ color, depthTest: false, transparent: true });
  const line = new THREE.Line(geometry, material);
  if (dashed) line.computeLineDistances();
  line.renderOrder = 10;
  return line;
}

/**
 * A confirmed or attached sketch drawn on its plane in the twin viewer
 * (RC-04): local strokes transformed by the plane frame into the viewer's
 * Y-up world. With an unknown scale it is drawn at an assumed, labelled
 * size. A reassign preview is drawn over it, dashed. Also registers "Normal
 * to sketch", which turns the camera only: the plane is never touched.
 */
export function SketchOnPlane({ plane, strokes }: SketchOnPlaneProps) {
  const modelRadius = useViewerStore((s) => s.modelBounds?.radius ?? null);
  const showGrid = useSketchPlaneUi((s) => s.showGrid);
  const showAxes = useSketchPlaneUi((s) => s.showAxes);
  const preview = useSketchPlaneUi((s) => s.preview);
  const register = useSketchPlaneUi((s) => s.registerNormalToSketch);
  const controls = useThree((s) => s.controls) as unknown as TinkercadControls | null;

  const placed = isPlaced(plane);
  const scale = useMemo(() => (plane ? displayScale(plane, modelRadius) : null), [plane, modelRadius]);
  const frame = useMemo(() => (plane && scale ? displayFrame(frameOf(plane), scale.frameScale) : null), [plane, scale]);

  const group = useMemo(() => {
    const g = new THREE.Group();
    g.name = 'sketch-on-plane';
    if (!placed || !frame || !scale) return g;
    for (const s of placeStrokes(strokes, frame, scale.scale)) {
      if (s.points.length > 1) g.add(polyline(s.points, STROKE_COLOR));
    }
    if (preview) {
      const target = displayFrame(frameOf(preview.target), scale.frameScale);
      for (const s of placeStrokes(preview.strokes, target, scale.scale)) {
        if (s.points.length > 1) g.add(polyline(s.points, PREVIEW_COLOR, true));
      }
    }
    const o = cadToViewer(frame.origin);
    const origin = new THREE.Vector3(o[0], o[1], o[2]);
    if (showAxes) {
      const len = scale.scale * 0.5;
      for (const [axis, color] of [
        [frame.u, 0xf87171],
        [frame.v, 0x4ade80],
        [frame.normal, 0x60a5fa],
      ] as const) {
        const d = cadToViewer(axis);
        g.add(new THREE.ArrowHelper(new THREE.Vector3(d[0], d[1], d[2]), origin, len, color));
      }
    }
    if (showGrid) {
      const size = scale.scale * 2;
      const grid = new THREE.GridHelper(size, 10, 0x888888, 0x444444);
      // GridHelper lies in its local XZ plane (normal +Y). Turn its basis onto (U, N, -V) in the viewer.
      const u = cadToViewer(frame.u);
      const n = cadToViewer(frame.normal);
      const v = cadToViewer(frame.v);
      const basis = new THREE.Matrix4().makeBasis(
        new THREE.Vector3(u[0], u[1], u[2]),
        new THREE.Vector3(n[0], n[1], n[2]),
        new THREE.Vector3(-v[0], -v[1], -v[2]),
      );
      grid.quaternion.setFromRotationMatrix(basis);
      grid.position.copy(origin);
      (grid.material as THREE.Material).transparent = true;
      (grid.material as THREE.Material).opacity = 0.35;
      g.add(grid);
    }
    return g;
  }, [placed, frame, scale, strokes, preview, showAxes, showGrid]);

  useEffect(
    () => () => {
      group.traverse((o) => {
        const mesh = o as THREE.Line;
        mesh.geometry?.dispose?.();
        const m = mesh.material as THREE.Material | THREE.Material[] | undefined;
        if (Array.isArray(m)) m.forEach((x) => x.dispose());
        else m?.dispose?.();
      });
    },
    [group],
  );

  // "Normal to sketch": turn the camera to face the plane. The camera only.
  useEffect(() => {
    if (!placed || !plane || !controls || typeof controls.setViewDirection !== 'function') {
      register(null);
      return;
    }
    const n = cadToViewer(plane.normal);
    register(() => controls.setViewDirection(new THREE.Vector3(n[0], n[1], n[2])));
    return () => register(null);
  }, [placed, plane, controls, register]);

  // The active orthographic view, read on demand for a proposal hint only.
  const camera = useThree((s) => s.camera);
  const registerHint = useSketchPlaneUi((s) => s.registerViewHint);
  useEffect(() => {
    registerHint(() => {
      const d = camera.getWorldDirection(new THREE.Vector3());
      return viewHintFromDirection(viewerToCad([d.x, d.y, d.z]));
    });
    return () => registerHint(null);
  }, [camera, registerHint]);

  if (!placed) return null;
  return <primitive object={group} />;
}
