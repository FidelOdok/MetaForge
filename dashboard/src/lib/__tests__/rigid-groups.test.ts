import { describe, it, expect } from 'vitest';
import { parseRigidGroups, groupForMesh } from '../rigid-groups';
import type { ModelManifest } from '../../types/viewer';

// Scene mesh names as the GLB exposes them (OCCT names them mesh_0..N-1).
const SCENE = ['mesh_0', 'mesh_1', 'mesh_2'];

function manifest(over: Partial<ModelManifest> = {}): ModelManifest {
  return {
    parts: [
      { name: 'Motor', meshName: 'Part_1', children: [] },
      { name: 'Shaft', meshName: 'Part_2', children: [] },
      { name: 'Bracket', meshName: 'Part_3', children: [] },
    ],
    meshToNodeMap: {},
    materials: [],
    stats: { triangleCount: 0, fileSize: 0 },
    ...over,
  };
}

describe('parseRigidGroups', () => {
  it('resolves declared groups by partIndices → scene mesh names (by index)', () => {
    const groups = parseRigidGroups(
      manifest({
        rigidGroups: [
          { name: 'motor_assembly', partIndices: [0, 1] },
          { name: 'bracket', partIndices: [2] },
        ],
      }),
      SCENE,
    );
    expect(groups).toEqual([
      { name: 'motor_assembly', meshNames: ['mesh_0', 'mesh_1'] },
      { name: 'bracket', meshNames: ['mesh_2'] },
    ]);
  });

  it('drops out-of-range indices and skips empty groups', () => {
    const groups = parseRigidGroups(
      manifest({
        rigidGroups: [
          { name: 'partial', partIndices: [0, 99] },
          { name: 'empty', partIndices: [42] },
        ],
      }),
      SCENE,
    );
    expect(groups).toEqual([{ name: 'partial', meshNames: ['mesh_0'] }]);
  });

  it('falls back to one group per scene mesh, labelled from the manifest part', () => {
    const groups = parseRigidGroups(manifest(), SCENE);
    expect(groups).toEqual([
      { name: 'Motor_group', meshNames: ['mesh_0'] },
      { name: 'Shaft_group', meshNames: ['mesh_1'] },
      { name: 'Bracket_group', meshNames: ['mesh_2'] },
    ]);
  });

  it('falls back to the mesh name when there is no matching manifest part', () => {
    const groups = parseRigidGroups(manifest({ parts: [] }), SCENE);
    expect(groups).toEqual([
      { name: 'mesh_0_group', meshNames: ['mesh_0'] },
      { name: 'mesh_1_group', meshNames: ['mesh_1'] },
      { name: 'mesh_2_group', meshNames: ['mesh_2'] },
    ]);
  });

  it('returns nothing when the scene has no meshes', () => {
    expect(parseRigidGroups(manifest(), [])).toEqual([]);
  });
});

describe('groupForMesh', () => {
  it('finds the group owning a scene mesh, or null', () => {
    const groups = parseRigidGroups(
      manifest({ rigidGroups: [{ name: 'motor_assembly', partIndices: [0, 1] }] }),
      SCENE,
    );
    expect(groupForMesh(groups, 'mesh_1')?.name).toBe('motor_assembly');
    expect(groupForMesh(groups, 'mesh_9')).toBeNull();
  });
});
