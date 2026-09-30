import { describe, expect, it } from 'vitest';
import { BoxGeometry, Mesh, MeshBasicMaterial, Object3D } from 'three';
import type { URDFJoint } from 'urdf-loader';
import { meshesOfJointChild } from '../robot-drag-controls';

function makeMesh(name: string): Mesh {
  const mesh = new Mesh(new BoxGeometry(1, 1, 1), new MeshBasicMaterial());
  mesh.name = name;
  return mesh;
}

describe('meshesOfJointChild', () => {
  it('collects meshes directly under the joint', () => {
    const joint = new Object3D() as unknown as URDFJoint;
    const link_mesh = makeMesh('link_2_visual');
    (joint as unknown as Object3D).add(link_mesh);

    expect(meshesOfJointChild(joint)).toEqual([link_mesh]);
  });

  it('stops descending at the next URDFJoint, excluding downstream links', () => {
    const joint = new Object3D() as unknown as URDFJoint;
    const link2Mesh = makeMesh('link_2_visual');
    (joint as unknown as Object3D).add(link2Mesh);

    const childJoint = new Object3D() as unknown as URDFJoint;
    (childJoint as unknown as { isURDFJoint: boolean }).isURDFJoint = true;
    const link3Mesh = makeMesh('link_3_visual');
    (childJoint as unknown as Object3D).add(link3Mesh);
    (joint as unknown as Object3D).add(childJoint as unknown as Object3D);

    const meshes = meshesOfJointChild(joint);
    expect(meshes).toEqual([link2Mesh]);
    expect(meshes).not.toContain(link3Mesh);
  });

  it('collects meshes nested inside a visual group under the joint', () => {
    const joint = new Object3D() as unknown as URDFJoint;
    const visualGroup = new Object3D();
    const nestedMesh = makeMesh('nested_visual');
    visualGroup.add(nestedMesh);
    (joint as unknown as Object3D).add(visualGroup);

    expect(meshesOfJointChild(joint)).toEqual([nestedMesh]);
  });

  it('returns an empty array for a joint with no child meshes', () => {
    const joint = new Object3D() as unknown as URDFJoint;
    expect(meshesOfJointChild(joint)).toEqual([]);
  });
});
