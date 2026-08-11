import { useState, useEffect } from 'react';
import * as THREE from 'three';
import { TransformControls } from '@react-three/drei';
import type { TransformMode, Vec3 } from '../../store/transient-transform-store';

interface TransformGizmoProps {
  /** World-space centroid of the selected group (gizmo origin). */
  centroid: [number, number, number];
  /** Active interaction mode — translate/rotate/scale. */
  mode: TransformMode;
  /** Called with the mode-appropriate delta as the gizmo is dragged. */
  onChange: (mode: TransformMode, value: Vec3) => void;
}

/**
 * Transform gizmo for a selected rigid group (MET-519, extended MET-611 to
 * rotate/scale). Wraps drei's `TransformControls` around an invisible proxy
 * object placed at the group centroid. Dragging the proxy reports a delta —
 * translation as (proxy − centroid), rotation/scale as the proxy's own Euler
 * angles / scale factors since the proxy always starts at identity rotation
 * and unit scale — to the caller, which applies it to the group's meshes; the
 * gizmo itself never touches the meshes (stateless by design; only the delta
 * matters). Because OrbitControls is `makeDefault`, drei auto-suppresses
 * orbit while dragging.
 */
export function TransformGizmo({ centroid, mode, onChange }: TransformGizmoProps) {
  const [proxy, setProxy] = useState<THREE.Object3D | null>(null);

  // Re-seat the proxy at the centroid (and reset rotation/scale to identity)
  // whenever the selection (centroid) changes, so each mode's delta is always
  // measured from a clean baseline.
  useEffect(() => {
    if (!proxy) return;
    proxy.position.set(centroid[0], centroid[1], centroid[2]);
    proxy.rotation.set(0, 0, 0);
    proxy.scale.set(1, 1, 1);
  }, [proxy, centroid]);

  const handleChange = () => {
    if (!proxy) return;
    if (mode === 'translate') {
      onChange('translate', [
        proxy.position.x - centroid[0],
        proxy.position.y - centroid[1],
        proxy.position.z - centroid[2],
      ]);
    } else if (mode === 'rotate') {
      onChange('rotate', [proxy.rotation.x, proxy.rotation.y, proxy.rotation.z]);
    } else {
      onChange('scale', [proxy.scale.x, proxy.scale.y, proxy.scale.z]);
    }
  };

  return (
    <>
      <object3D ref={setProxy} />
      {proxy && (
        <TransformControls object={proxy} mode={mode} onObjectChange={handleChange} />
      )}
    </>
  );
}
