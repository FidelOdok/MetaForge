import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '../../../test/test-utils';
import type { CandidateFace, FaceList, SketchPlane, SketchPlaneView } from '../../../api/endpoints/sketchPlanes';
import {
  faceLabel,
  faceTriangles,
  pickFace,
  trianglesByGeometry,
  verifiedRange,
  type MeshLike,
} from '../../../lib/sketch/faceMesh';

const getAttachableGeometry = vi.fn();
const getCandidateFaces = vi.fn();
const attachSketchToFace = vi.fn();
const resolveReattachment = vi.fn();
vi.mock('../../../api/endpoints/sketchPlanes', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/sketchPlanes')>()),
  getAttachableGeometry: (...a: unknown[]) => getAttachableGeometry(...a),
  getCandidateFaces: (...a: unknown[]) => getCandidateFaces(...a),
  attachSketchToFace: (...a: unknown[]) => attachSketchToFace(...a),
  resolveReattachment: (...a: unknown[]) => resolveReattachment(...a),
  proposeSketchPlane: vi.fn().mockResolvedValue(null),
}));
vi.mock('../../../api/endpoints/twin', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/twin')>()),
  getNodeModel: vi.fn().mockResolvedValue({ hash: 'h', glb_url: '/v1/convert/h/glb', metadata: { parts: [], materials: [], stats: {} } }),
}));
// The WebGL canvas cannot run in jsdom; the list beside it picks the same faces.
vi.mock('../FacePickCanvas', () => ({ default: () => <div data-testid="face-canvas" /> }));

import { PlanePrompt } from '../PlanePrompt';
import { SketchPlaneControls } from '../SketchPlaneControls';
import { ReattachChoices, REATTACH_MESSAGE } from '../ReattachChoices';

const face = (id: string, normal: [number, number, number], offset: number, start: number, extra: Partial<CandidateFace> = {}): CandidateFace => ({
  face_id: id,
  normal,
  offset,
  area: 600,
  centroid: [10, 15, offset],
  bounding_box: { min_x: 0, min_y: 0, min_z: offset, max_x: 20, max_y: 30, max_z: offset },
  outer_loop_edges: 4,
  longest_edge: { direction: [0, 1, 0], length: 30 },
  adjacent: [],
  mesh: { part_index: 0, face_index: start / 2, triangle_start: start, triangle_count: 2 },
  semantic_tag: null,
  signature_summary: 'normal (0, 0, 1); area 600',
  ...extra,
});

const faceList = (faces: CandidateFace[]): FaceList => ({
  project_id: 'p1',
  item_key: 'CAD-BASE',
  revision: 3,
  item_ref: 'CAD-BASE@3',
  node_id: 'node-base-3',
  geometry_state: 'validated',
  part_size: 38,
  solid_count: 1,
  face_count: faces.length,
  faces,
  mesh_map: Object.fromEntries(faces.map((f) => [f.face_id, f.mesh!])),
  mesh_source: '',
  cached: false,
});

const facePlane = (state: SketchPlane['state'], pending: Record<string, unknown> | null = null): SketchPlane => ({
  plane_id: 'PLN-SKETCH',
  owner_component: 'CAD-BASE',
  kind: 'face',
  principal: null,
  origin: [10, 15, 10],
  u_axis: [0, 1, 0],
  v_axis: [-1, 0, 0],
  normal: [0, 0, 1],
  unit: 'mm',
  scale: null,
  transform: null,
  source_revision: 'SKT-SKETCH@1',
  attachment: {
    item_key: 'CAD-BASE',
    revision: 2,
    face_ref: 's0f5',
    signature: {
      semantic_tag: 'PSK-BOX:F1:top',
      normal: [0, 0, 1],
      offset: 10,
      area: 600,
      centroid: [10, 15, 10],
      loop_edges: 4,
      adjacent_normals: [],
      adjacent_curved: 0,
      part_size: 38,
      face_id: 's0f5',
    },
    remapped_from: null,
    pending: pending as never,
  },
  state,
  proposed_by: 'user',
  confidence: 1,
});

const viewOf = (plane: SketchPlane): SketchPlaneView => ({
  sketch_key: 'SKT-SKETCH',
  sketch_ref: 'SKT-SKETCH@1',
  plane,
  item_key: 'PLN-SKETCH',
  revision: 2,
  item_ref: 'PLN-SKETCH@2',
  node_id: 'n2',
  parent_revision: 'PLN-SKETCH@1',
  pinned_sketch_ref: 'SKT-SKETCH@1',
  action: 'reattachment_required',
  can_undo: true,
  mode: null,
});

const changeResult = (plane: SketchPlane) => ({ ...viewOf(plane), operation_id: 'job:x', duplicate: false, unchanged: false, sketch_revision_written: null });

const pending = {
  outcome: 'ambiguous',
  revision: 3,
  item_ref: 'CAD-BASE@3',
  node_id: 'node-base-3',
  candidates: [
    { face_id: 's0f6', score: 0.7, tag: 'unknown' },
    { face_id: 's0f7', score: 0.7, tag: 'unknown' },
  ],
  reason: 's0f6 and s0f7 match about equally',
  at: '2026-11-04T00:00:00Z',
};

beforeEach(() => {
  getAttachableGeometry.mockReset();
  getCandidateFaces.mockReset().mockResolvedValue(faceList([face('s0f4', [0, 0, -1], 0, 8), face('s0f5', [0, 0, 1], 10, 10)]));
  attachSketchToFace.mockReset().mockResolvedValue(changeResult(facePlane('ATTACHED')));
  resolveReattachment.mockReset().mockResolvedValue(changeResult(facePlane('ATTACHED')));
});

describe('Select surface', () => {
  it('stays disabled with the reason when there is no validated exact geometry', async () => {
    getAttachableGeometry.mockResolvedValue({ project_id: 'p1', items: [], reason: 'There is no validated exact geometry in this project yet.' });
    render(<PlanePrompt projectId="p1" sketchKey="SKT-SKETCH" onDone={() => undefined} />);
    const reason = await screen.findByTestId('select-surface-reason');
    expect(reason).toHaveTextContent('no validated exact geometry');
    expect(screen.getByTestId('select-surface')).toBeDisabled();
  });

  it('highlights a face under the pointer and attaches the one clicked', async () => {
    getAttachableGeometry.mockResolvedValue({
      project_id: 'p1',
      items: [{ item_key: 'CAD-BASE', name: 'Base', revision: 3, item_ref: 'CAD-BASE@3', node_id: 'node-base-3', geometry_state: 'validated' }],
      reason: null,
    });
    const onDone = vi.fn();
    render(<PlanePrompt projectId="p1" sketchKey="SKT-SKETCH" onDone={onDone} />);
    await waitFor(() => expect(screen.getByTestId('select-surface')).toBeEnabled());
    fireEvent.click(screen.getByTestId('select-surface'));
    const top = await screen.findByTestId('face-option-s0f5');
    expect(await screen.findByTestId('face-canvas')).toBeInTheDocument();
    fireEvent.mouseEnter(top);
    expect(top).toHaveAttribute('aria-pressed', 'true');
    expect(top.className).toMatch(/is-hovered/);
    fireEvent.click(top);
    await waitFor(() => expect(attachSketchToFace).toHaveBeenCalled());
    const [, sketchKey, body] = attachSketchToFace.mock.calls[0] as [string, string, Record<string, unknown>];
    expect(sketchKey).toBe('SKT-SKETCH');
    expect(body).toMatchObject({ item_key: 'CAD-BASE', revision: 3, face_id: 's0f5' });
    expect(String(body.op_id).length).toBeGreaterThanOrEqual(8);
    await waitFor(() => expect(onDone).toHaveBeenCalledWith('confirmed'));
  });
});

describe('REATTACHMENT_REQUIRED', () => {
  it('Engineering shows the attachment, the state and the candidate faces, and detaches on request', async () => {
    getAttachableGeometry.mockResolvedValue({ project_id: 'p1', items: [], reason: null });
    getCandidateFaces.mockResolvedValue(
      faceList([face('s0f6', [0, 0, 1], 10, 12), face('s0f7', [0, 0, 1], 10, 14), face('s0f4', [0, 0, -1], 0, 8)]),
    );
    render(<SketchPlaneControls projectId="p1" sketchKey="SKT-SKETCH" view={viewOf(facePlane('REATTACHMENT_REQUIRED', pending))} />);
    expect(screen.getByTestId('sketch-plane-state')).toHaveTextContent('REATTACHMENT_REQUIRED');
    expect(screen.getByTestId('sketch-plane-attachment')).toHaveTextContent('On CAD-BASE@2, face s0f5');
    expect(screen.getByTestId('sketch-plane-attachment')).toHaveTextContent('made by PSK-BOX:F1:top');
    expect(screen.getByTestId('reattach-detail')).toHaveTextContent('2 surfaces of CAD-BASE@3 could be it');
    expect(screen.queryByRole('button', { name: 'Choose a plane' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Pick the surface' }));
    expect(await screen.findByTestId('face-option-s0f6')).toHaveClass('is-candidate');
    expect(screen.getByTestId('face-option-s0f7')).toHaveClass('is-candidate');
    expect(screen.getByTestId('face-option-s0f4')).not.toHaveClass('is-candidate');
    expect(getCandidateFaces).toHaveBeenCalledWith('p1', 'CAD-BASE', 3, expect.anything());
    fireEvent.click(screen.getByTestId('face-option-s0f7'));
    await waitFor(() => expect(resolveReattachment).toHaveBeenCalled());
    expect(resolveReattachment.mock.calls[0]?.[2]).toMatchObject({ action: 'pick_face', face_id: 's0f7', revision: 3 });

    fireEvent.click(screen.getByRole('button', { name: 'Top' }));
    await waitFor(() => expect(resolveReattachment).toHaveBeenCalledTimes(2));
    expect(resolveReattachment.mock.calls[1]?.[2]).toMatchObject({ action: 'detach_principal', principal: 'XY' });
  });

  it('Create says it in plain words and can keep it as a concept or decide later', async () => {
    const onClose = vi.fn();
    render(<ReattachChoices projectId="p1" sketchKey="SKT-SKETCH" plane={facePlane('REATTACHMENT_REQUIRED', pending)} onClose={onClose} />);
    expect(screen.getByTestId('reattach-message')).toHaveTextContent(REATTACH_MESSAGE);
    expect(screen.queryByTestId('reattach-detail')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Keep as concept' }));
    await waitFor(() => expect(resolveReattachment).toHaveBeenCalled());
    expect(resolveReattachment.mock.calls[0]?.[2]).toMatchObject({ action: 'detach_unassigned' });
    fireEvent.click(screen.getByRole('button', { name: 'Decide later' }));
    expect(onClose).toHaveBeenCalled();
  });
});

describe('faceMesh', () => {
  // A 20 x 30 x 10 box's bottom (triangles 0-1) and top (2-3), and one side triangle (4).
  const positions = [
    0, 0, 0, 20, 0, 0, 20, 30, 0, 0, 30, 0, // bottom 0-3
    0, 0, 10, 20, 0, 10, 20, 30, 10, 0, 30, 10, // top 4-7
    0, 0, 0, 0, 30, 0, 0, 30, 10, // side 8-10
  ];
  const mesh: MeshLike = { positions, index: [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 8, 9, 10] };
  const bottom = face('b', [0, 0, -1], 0, 0);
  const top = face('t', [0, 0, 1], 10, 2);

  it('trusts a range only when every triangle lies on the face', () => {
    expect(verifiedRange(mesh, top, 1e-3)).toEqual([2, 3]);
    const wrong = { ...top, mesh: { part_index: 0, face_index: 0, triangle_start: 0, triangle_count: 2 } };
    expect(verifiedRange(mesh, wrong, 1e-3)).toBeNull();
    // Falls back to geometry: still the top's own triangles.
    expect(faceTriangles(mesh, wrong, 1e-3)).toEqual([2, 3]);
    expect(trianglesByGeometry(mesh, bottom, 1e-3)).toEqual([0, 1]);
  });

  it('picks the face under a triangle, and nothing on a face it does not know', () => {
    expect(pickFace(mesh, [bottom, top], 3, 1e-3)?.face_id).toBe('t');
    expect(pickFace(mesh, [bottom, top], 0, 1e-3)?.face_id).toBe('b');
    expect(pickFace(mesh, [bottom, top], 4, 1e-3)).toBeNull();
  });

  it('names a face by its feature, else by the way it faces', () => {
    expect(faceLabel({ ...top, semantic_tag: 'PSK-BOX:F1:side:r1.0' })).toBe('side r1.0 of PSK-BOX');
    expect(faceLabel(top)).toBe('top face');
    expect(faceLabel({ ...top, normal: [0.6, 0, 0.8] })).toBe('slanted face');
  });
});
