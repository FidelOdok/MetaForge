import { useRef, useMemo, useEffect } from 'react';
import { useFrame, ThreeEvent } from '@react-three/fiber';
import { useGLTF } from '@react-three/drei';
import * as THREE from 'three';
import { useViewerStore } from '../../store/viewer-store';
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
      }
    });

    meshMapRef.current = map;
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

  // Smooth exploded view animation
  const targetPositions = useRef(new Map<string, THREE.Vector3>());

  useEffect(() => {
    const targets = new Map<string, THREE.Vector3>();
    for (const [name, entry] of meshMapRef.current) {
      const offset = entry.center.clone().sub(assemblyCenter).multiplyScalar(explodeFactor * 2);
      targets.set(name, offset);
    }
    targetPositions.current = targets;
  }, [explodeFactor, assemblyCenter]);

  useFrame(() => {
    for (const [name, entry] of meshMapRef.current) {
      const target = targetPositions.current.get(name);
      if (target) {
        entry.mesh.position.lerp(target, 0.1);
      }
    }
  });

  const handleClick = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    const mesh = e.object as THREE.Mesh;
    const meshName = mesh.name || mesh.parent?.name || '';
    const entry = meshMapRef.current.get(meshName);
    if (entry && onPartClick) {
      const nodeId = manifest.meshToNodeMap[meshName];
      onPartClick({
        meshName,
        name: entry.name,
        nodeId,
        boundingBox: entry.center
          ? undefined
          : undefined,
      });
    }
  };

  return (
    <group ref={groupRef}>
      <primitive object={scene} onClick={handleClick} />
    </group>
  );
}
