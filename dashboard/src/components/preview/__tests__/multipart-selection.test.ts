import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { pickedComponent } from '../engines/ModelPreview';
import { componentOfObject } from '../../../store/component-selection-store';
import type { TwinNode } from '../../../types/twin';

/**
 * A multi-part CAD model selects by component (SYNC-001): the OCCT converter
 * writes each part's `component_id` into its GLB node extras, which three's
 * GLTFLoader turns into `userData`. Parsed here with the real loader, from a
 * glTF shaped like the converter's (one node and one triangle mesh per part).
 */

const TRIANGLE = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);

function gltf(parts: { name: string; extras?: Record<string, unknown>; translation?: number[] }[]): string {
  const bytes = new Uint8Array(TRIANGLE.buffer);
  let binary = '';
  bytes.forEach((b) => (binary += String.fromCharCode(b)));
  return JSON.stringify({
    asset: { version: '2.0' },
    scene: 0,
    scenes: [{ nodes: parts.map((_, i) => i) }],
    nodes: parts.map((p) => ({ name: p.name, mesh: 0, ...(p.extras ? { extras: p.extras } : {}), ...(p.translation ? { translation: p.translation } : {}) })),
    meshes: [{ primitives: [{ attributes: { POSITION: 0 } }] }],
    buffers: [{ byteLength: TRIANGLE.byteLength, uri: `data:application/octet-stream;base64,${btoa(binary)}` }],
    bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: TRIANGLE.byteLength }],
    accessors: [{ bufferView: 0, componentType: 5126, count: 3, type: 'VEC3', min: [0, 0, 0], max: [1, 1, 0] }],
  });
}

function parse(json: string): Promise<THREE.Group> {
  return new Promise((resolve, reject) => {
    new GLTFLoader().parse(json, '', (result) => resolve(result.scene), reject);
  });
}

const node: TwinNode = {
  id: 'cad-1',
  name: 'Enclosure',
  type: 'work_product' as TwinNode['type'],
  domain: 'mechanical',
  status: 'validated',
  properties: {},
  updatedAt: '',
  projectId: 'p1',
  representation: {
    level: 'L3',
    authority: 'validated',
    label: 'Validated',
    source_revision: 'CAD-ENC@2',
    component_ids: ['CMP-lid', 'CMP-base'],
    generated_by: {},
    derived: true,
    display_rank: 5,
  },
};

describe('multi-part selection by component id', () => {
  it('picks each part of a converter GLB as its own component', async () => {
    const scene = await parse(
      gltf([
        { name: 'lid', extras: { component_id: 'CMP-lid', part_name: 'lid' }, translation: [0, 0, 10] },
        { name: 'base', extras: { component_id: 'CMP-base', part_name: 'base' } },
        { name: 'Part_3' },
      ]),
    );
    const meshes: THREE.Object3D[] = [];
    scene.traverse((o) => {
      if ((o as THREE.Mesh).isMesh) meshes.push(o);
    });
    expect(meshes).toHaveLength(3);
    const byName = Object.fromEntries(meshes.map((m) => [m.name, m]));
    expect(pickedComponent(byName.lid, node)).toEqual({ componentId: 'CMP-lid', name: 'lid' });
    expect(pickedComponent(byName.base, node)).toEqual({ componentId: 'CMP-base', name: 'base' });
    // A part with no id in a model of two components names none: never a guess.
    expect(pickedComponent(byName.Part_3, node)).toBeNull();
  });

  it('finds the component through a parent node, and falls back for a single-part model', async () => {
    const parent = new THREE.Group();
    parent.userData = { component_id: 'CMP-lid', part_name: 'lid' };
    const child = new THREE.Mesh();
    parent.add(child);
    expect(componentOfObject(child)).toEqual({ componentId: 'CMP-lid', name: 'lid' });

    const single: TwinNode = { ...node, representation: { ...node.representation!, component_ids: ['CMP-bracket'] } };
    const scene = await parse(gltf([{ name: 'Part_1' }]));
    const mesh = scene.children[0]!;
    expect(pickedComponent(mesh, single)).toEqual({ componentId: 'CMP-bracket', name: 'Enclosure' });
  });
});
