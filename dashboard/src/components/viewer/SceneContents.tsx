import { useRef, useMemo, useEffect, useState } from 'react';
import { useFrame, ThreeEvent } from '@react-three/fiber';
import { useGLTF } from '@react-three/drei';
import * as THREE from 'three';
import { useViewerStore } from '../../store/viewer-store';
import { useTransientTransform } from '../../store/transient-transform-store';
import { parseRigidGroups, groupForMesh } from '../../lib/rigid-groups';
import { TransformGizmo } from './TransformGizmo';
import type { PartInfo, ModelManifest } from '../../types/viewer';

const HIGHLIGHT_COLOR = new THREE.Color(0x3b82f6);
const HIGHLIGHT_OPACITY = 0.4;

interface SceneContentsProps {
  glbUrl: string;
  manifest: ModelManifest;
  onPartClick?: (part: PartInfo) => void;
}

interface MeshEntry {
  mesh: THREE.Mesh;
  name: string;
  meshName: string;
  originalMaterial: THREE.Material | THREE.Material[];
  center: THREE.Vector3;
}

export function SceneContents({ glbUrl, manifest, onPartClick }: SceneContentsProps) {
  const { scene } = useGLTF(glbUrl);
  const groupRef = useRef<THREE.Group>(null);
  const meshMapRef = useRef<Map<string, MeshEntry>>(new Map());

  const selectedMeshName = useViewerStore((s) => s.selectedMeshName);
  const hiddenMeshes = useViewerStore((s) => s.hiddenMeshes);
  const explodeFactor = useViewerStore((s) => s.explodeFactor);

  // Rigid-group manipulation (MET-519). Groups resolve against the *actual*
  // GLB scene mesh names (captured below), not the manifest part names — the
  // OCCT converter names parts Part_N but meshes mesh_N, so name-matching never
  // found the clicked mesh (MET-522). Subscribe only to the selected group
  // (changes rarely); the per-frame delta is read via getState() in useFrame to
  // avoid a React re-render on every drag tick.
  const [sceneMeshNames, setSceneMeshNames] = useState<string[]>([]);
  const resolvedGroups = useMemo(
    () => parseRigidGroups(manifest, sceneMeshNames),
    [manifest, sceneMeshNames],
  );
  const selectedGroup = useTransientTransform((s) => s.selectedGroup);
  const selectGroup = useTransientTransform((s) => s.selectGroup);
  const setDelta = useTransientTransform((s) => s.setDelta);
  const [gizmoCentroid, setGizmoCentroid] = useState<[number, number, number] | null>(null);

  const memberMeshes = useMemo(() => {
    const g = resolvedGroups.find((rg) => rg.name === selectedGroup);
    return new Set(g?.meshNames ?? []);
  }, [resolvedGroups, selectedGroup]);

  const highlightMaterial = useMemo(
    () =>
      new THREE.MeshStandardMaterial({
        color: HIGHLIGHT_COLOR,
        transparent: true,
        opacity: HIGHLIGHT_OPACITY,
        depthWrite: false,
      }),
    [],
  );

  // Compute assembly center for exploded view
  const assemblyCenter = useMemo(() => {
    const box = new THREE.Box3();
    scene.traverse((child) => {
      if ((child as THREE.Mesh).isMesh) {
        box.expandByObject(child);
      }
    });
    const center = new THREE.Vector3();
    box.getCenter(center);
    return center;
  }, [scene]);

  // Build mesh map on mount
  useEffect(() => {
    const map = new Map<string, MeshEntry>();
    const partLookup = new Map<string, string>();
    const orderedNames: string[] = [];

    // Build a lookup from mesh scene name to manifest part name
    for (const part of manifest.parts) {
      partLookup.set(part.meshName, part.name);
    }

    scene.traverse((child) => {
      if ((child as THREE.Mesh).isMesh) {
        const mesh = child as THREE.Mesh;
        const meshName = mesh.name || mesh.parent?.name || `unnamed_${map.size}`;
        const partName = partLookup.get(meshName) || meshName;

        const box = new THREE.Box3().setFromObject(mesh);
        const center = new THREE.Vector3();
        box.getCenter(center);

        map.set(meshName, {
          mesh,
          name: partName,
          meshName,
          originalMaterial: mesh.material,
          center,
        });
        orderedNames.push(meshName);
      }
    });

    meshMapRef.current = map;
    // Ordered scene mesh names back the rigid-group resolution (MET-522).
    setSceneMeshNames(orderedNames);
  }, [scene, manifest]);

  // Update highlight and visibility
  useEffect(() => {
    for (const [name, entry] of meshMapRef.current) {
      entry.mesh.visible = !hiddenMeshes.has(name);
      if (name === selectedMeshName) {
        entry.mesh.material = highlightMaterial;
      } else {
        entry.mesh.material = entry.originalMaterial;
      }
    }
  }, [selectedMeshName, hiddenMeshes, highlightMaterial]);

  // Gizmo centroid = mean of the selected group's member-mesh centers (MET-519).
  useEffect(() => {
    if (!selectedGroup || memberMeshes.size === 0) {
      setGizmoCentroid(null);
      return;
    }
    const acc = new THREE.Vector3();
    let n = 0;
    for (const name of memberMeshes) {
      const entry = meshMapRef.current.get(name);
      if (entry) {
        acc.add(entry.center);
        n += 1;
      }
    }
    setGizmoCentroid(n > 0 ? [acc.x / n, acc.y / n, acc.z / n] : null);
  }, [selectedGroup, memberMeshes, scene]);

  // Smooth exploded view animation
  const targetPositions = useRef(new Map<string, THREE.Vector3>());
  const tmpTarget = useRef(new THREE.Vector3());

  useEffect(() => {
    const targets = new Map<string, THREE.Vector3>();
    for (const [name, entry] of meshMapRef.current) {
      const offset = entry.center.clone().sub(assemblyCenter).multiplyScalar(explodeFactor * 2);
      targets.set(name, offset);
    }
    targetPositions.current = targets;
  }, [explodeFactor, assemblyCenter]);

  useFrame(() => {
    // Read the live drag delta without subscribing (avoids per-frame React renders).
    const delta = useTransientTransform.getState().delta;
    for (const [name, entry] of meshMapRef.current) {
      const base = targetPositions.current.get(name);
      const t = tmpTarget.current.set(base?.x ?? 0, base?.y ?? 0, base?.z ?? 0);
      if (memberMeshes.has(name)) {
        t.x += delta[0];
        t.y += delta[1];
        t.z += delta[2];
      }
      entry.mesh.position.lerp(t, 0.3);
    }
  });

  const handleClick = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    const mesh = e.object as THREE.Mesh;
    const meshName = mesh.name || mesh.parent?.name || '';
    const entry = meshMapRef.current.get(meshName);
    // Select the rigid group this mesh belongs to so the gizmo can drive it.
    selectGroup(groupForMesh(resolvedGroups, meshName)?.name ?? null);
    if (entry && onPartClick) {
      const nodeId = manifest.meshToNodeMap[meshName];
      onPartClick({
        meshName,
        name: entry.name,
        nodeId,
        boundingBox: entry.center ? undefined : undefined,
      });
    }
  };

  return (
    <group ref={groupRef}>
      <primitive object={scene} onClick={handleClick} />
      {gizmoCentroid && <TransformGizmo centroid={gizmoCentroid} onDelta={setDelta} />}
    </group>
  );
}
