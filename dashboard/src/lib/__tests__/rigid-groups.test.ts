import { describe, it, expect } from 'vitest';
import { parseRigidGroups, groupForMesh } from '../rigid-groups';
import type { ModelManifest } from '../../types/viewer';

function manifest(over: Partial<ModelManifest> = {}): ModelManifest {
  return {
    parts: [
      { name: 'Motor', meshName: 'mesh_motor', children: [] },
      { name: 'Shaft', meshName: 'mesh_shaft', children: [] },
      { name: 'Bracket', meshName: 'mesh_bracket', children: [] },
    ],
    meshToNodeMap: {},
    materials: [],
    stats: { triangleCount: 0, fileSize: 0 },
    ...over,
  };
}

describe('parseRigidGroups', () => {
  it('resolves declared groups by partIndices → meshNames', () => {
    const groups = parseRigidGroups(
      manifest({
        rigidGroups: [
          { name: 'motor_assembly', partIndices: [0, 1] },
          { name: 'bracket', partIndices: [2] },
        ],
      }),
    );
    expect(groups).toEqual([
      { name: 'motor_assembly', meshNames: ['mesh_motor', 'mesh_shaft'] },
      { name: 'bracket', meshNames: ['mesh_bracket'] },
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
    );
    expect(groups).toEqual([{ name: 'partial', meshNames: ['mesh_motor'] }]);
  });

  it('falls back to one single-part group per part when none declared', () => {
    const groups = parseRigidGroups(manifest());
    expect(groups).toEqual([
      { name: 'Motor_group', meshNames: ['mesh_motor'] },
      { name: 'Shaft_group', meshNames: ['mesh_shaft'] },
      { name: 'Bracket_group', meshNames: ['mesh_bracket'] },
    ]);
  });

  it('falls back when rigidGroups is an empty array', () => {
    const groups = parseRigidGroups(manifest({ rigidGroups: [] }));
    expect(groups).toHaveLength(3);
    expect(groups[0]?.name).toBe('Motor_group');
  });
});

describe('groupForMesh', () => {
  it('finds the group owning a mesh, or null', () => {
    const groups = parseRigidGroups(
      manifest({ rigidGroups: [{ name: 'motor_assembly', partIndices: [0, 1] }] }),
    );
    expect(groupForMesh(groups, 'mesh_shaft')?.name).toBe('motor_assembly');
    expect(groupForMesh(groups, 'mesh_nope')).toBeNull();
  });
});
