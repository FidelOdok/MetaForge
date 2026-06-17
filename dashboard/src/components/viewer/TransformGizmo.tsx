import { useState, useEffect } from 'react';
import * as THREE from 'three';
import { TransformControls } from '@react-three/drei';
import type { Vec3 } from '../../store/transient-transform-store';

interface TransformGizmoProps {
  /** World-space centroid of the selected group (gizmo origin). */
  centroid: [number, number, number];
  /** Called with the translation delta (world units) as the gizmo is dragged. */
  onDelta: (delta: Vec3) => void;
}

/**
 * Translate gizmo for a selected rigid group (MET-519, Phase 1.5).
 *
 * Wraps drei's `TransformControls` around an invisible proxy object placed at
 * the group centroid. Dragging the proxy reports a delta (proxy − centroid) to
 * the caller, which applies it to the group's meshes — the gizmo itself never
 * touches the meshes (stateless by design; only the delta matters). Because the
 * OrbitControls is `makeDefault`, drei auto-suppresses orbit while dragging.
 *
 * Rotation handles are Phase 2; this is translate-only.
 */
export function TransformGizmo({ centroid, onDelta }: TransformGizmoProps) {
  const [proxy, setProxy] = useState<THREE.Object3D | null>(null);

  // Re-seat the proxy at the centroid whenever the selection (centroid) changes.
  useEffect(() => {
    if (proxy) proxy.position.set(centroid[0], centroid[1], centroid[2]);
  }, [proxy, centroid]);

  const handleChange = () => {
    if (!proxy) return;
    onDelta([
      proxy.position.x - centroid[0],
      proxy.position.y - centroid[1],
      proxy.position.z - centroid[2],
    ]);
  };

  return (
    <>
      <object3D ref={setProxy} />
      {proxy && (
        <TransformControls object={proxy} mode="translate" onObjectChange={handleChange} />
      )}
    </>
  );
}
