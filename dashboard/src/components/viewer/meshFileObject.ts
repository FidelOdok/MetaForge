import { useMemo } from 'react';
import { useLoader } from '@react-three/fiber';
import * as THREE from 'three';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import { ThreeMFLoader } from 'three/examples/jsm/loaders/3MFLoader.js';

/**
 * FORGE-531: STL and 3MF meshes loaded straight into the main viewer with
 * three's own loaders, no STEP->GLB converter round trip. 3MF carries its
 * own materials. STL has no colour at all, so it gets the same uniform
 * neutral the converter uses for an uncoloured STEP: no colour is invented.
 */
const STL_MATERIAL = new THREE.MeshStandardMaterial({ color: 0xb4b4b4, metalness: 0.1, roughness: 0.6 });

export function useMeshFileObject(url: string, format: string): THREE.Object3D {
  const loaded = useLoader(
    (format === '3mf' ? ThreeMFLoader : STLLoader) as typeof STLLoader,
    url,
  ) as unknown as THREE.BufferGeometry | THREE.Group;
  return useMemo(() => {
    if ((loaded as THREE.Group).isObject3D) return loaded as THREE.Group;
    const group = new THREE.Group();
    const mesh = new THREE.Mesh(loaded as THREE.BufferGeometry, STL_MATERIAL);
    mesh.name = url.split('/').pop()?.split('?')[0] || 'mesh';
    group.add(mesh);
    return group;
  }, [loaded, url]);
}

