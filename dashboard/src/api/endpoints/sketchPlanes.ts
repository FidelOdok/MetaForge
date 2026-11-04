import apiClient from '../client';
import type { Principal, ReassignMode, Vec3 } from '../../lib/sketch/planeMath';

/**
 * The plane a sketch is placed on (FORGE-790).
 *
 * `GET .../plane` reads; `propose` and `reassign/preview` never write. Only
 * `confirm`, `reassign`, `undo` and `archive` change the plane, each as the
 * next revision of the sketch's `sketch_plane` item. Nothing here sends the
 * camera: moving the view never changes a plane.
 */

const enc = encodeURIComponent;

export type PlaneState =
  | 'UNASSIGNED'
  | 'PROPOSED'
  | 'CONFIRMED'
  | 'ATTACHED'
  | 'INVALID'
  | 'REATTACHMENT_REQUIRED'
  | 'ARCHIVED';
export type ProposedBy = 'user' | 'face' | 'existing_sketch' | 'view' | 'component_frame' | 'model';
export type PlaneUnit = 'mm' | 'in' | 'unknown';
export type ViewHint = 'front' | 'side' | 'top';

export interface SketchPlane {
  plane_id: string;
  owner_component: string | null;
  kind: 'unassigned' | 'principal' | 'face' | 'offset' | 'custom';
  principal: Principal | null;
  origin: Vec3;
  u_axis: Vec3;
  v_axis: Vec3;
  normal: Vec3;
  unit: PlaneUnit;
  scale: number | null;
  transform: number[][] | null;
  source_revision: string | null;
  attachment: { item_key: string; revision: number | null; face_ref: string; signature: unknown } | null;
  state: PlaneState;
  proposed_by: ProposedBy | null;
  confidence: number;
}

export interface SketchPlaneView {
  sketch_key: string;
  sketch_ref: string | null;
  plane: SketchPlane;
  item_key: string | null;
  revision: number | null;
  item_ref: string | null;
  node_id: string | null;
  parent_revision: string | null;
  pinned_sketch_ref: string | null;
  action: string | null;
  can_undo: boolean;
  mode: string | null;
}

export interface PlaneChangeResult extends SketchPlaneView {
  operation_id: string;
  duplicate: boolean;
  unchanged: boolean;
  sketch_revision_written: string | null;
}

export interface PlaneCandidate {
  principal: Principal;
  view_name: string;
  proposed_by: ProposedBy;
  confidence: number;
  reason: string;
}

export interface PlaneProposal {
  sketch_key: string;
  sketch_ref: string | null;
  candidate: SketchPlane;
  candidates: PlaneCandidate[];
  ambiguous: boolean;
  requires_confirmation: boolean;
  current: SketchPlane | null;
  notes: string[];
}

export interface ProposalBody {
  explicit?: Principal | null;
  view_hint?: ViewHint | null;
  owner_component?: string | null;
  hint_text?: string | null;
}

export interface ConfirmBody {
  op_id: string;
  principal: Principal;
  origin?: Vec3;
  unit?: PlaneUnit;
  owner_component?: string | null;
  proposed_by?: ProposedBy;
  confidence?: number | null;
  base_revision?: string | null;
}

export interface ReassignBody {
  op_id: string;
  target: Principal;
  origin?: Vec3 | null;
  mode: ReassignMode;
  base_revision?: string | null;
}

export interface ReassignPreview {
  sketch_key: string;
  sketch_ref: string | null;
  mode: ReassignMode;
  source: SketchPlane;
  target: SketchPlane;
  strokes: { id: string; points: [number, number][]; off_plane: number }[];
  max_off_plane: number;
  exact: boolean;
  rewrites_strokes: boolean;
  committable: boolean;
  warnings: string[];
}

const base = (projectId: string, sketchKey: string) => `/twin/projects/${enc(projectId)}/sketches/${enc(sketchKey)}/plane`;

export async function getSketchPlane(projectId: string, sketchKey: string, signal?: AbortSignal): Promise<SketchPlaneView> {
  const { data } = await apiClient.get<SketchPlaneView>(base(projectId, sketchKey), { signal });
  return data;
}

export async function proposeSketchPlane(projectId: string, sketchKey: string, body: ProposalBody): Promise<PlaneProposal> {
  const { data } = await apiClient.post<PlaneProposal>(`${base(projectId, sketchKey)}/propose`, body);
  return data;
}

export async function confirmSketchPlane(projectId: string, sketchKey: string, body: ConfirmBody): Promise<PlaneChangeResult> {
  const { data } = await apiClient.post<PlaneChangeResult>(`${base(projectId, sketchKey)}/confirm`, body);
  return data;
}

export async function previewReassign(projectId: string, sketchKey: string, body: ReassignBody): Promise<ReassignPreview> {
  const { data } = await apiClient.post<ReassignPreview>(`${base(projectId, sketchKey)}/reassign/preview`, body);
  return data;
}

export async function reassignSketchPlane(projectId: string, sketchKey: string, body: ReassignBody): Promise<PlaneChangeResult> {
  const { data } = await apiClient.post<PlaneChangeResult>(`${base(projectId, sketchKey)}/reassign`, body);
  return data;
}

export async function undoSketchPlane(projectId: string, sketchKey: string, body: { op_id: string; base_revision?: string | null }): Promise<PlaneChangeResult> {
  const { data } = await apiClient.post<PlaneChangeResult>(`${base(projectId, sketchKey)}/undo`, body);
  return data;
}

export async function archiveSketchPlane(projectId: string, sketchKey: string, body: { op_id: string; base_revision?: string | null }): Promise<PlaneChangeResult> {
  const { data } = await apiClient.post<PlaneChangeResult>(`${base(projectId, sketchKey)}/archive`, body);
  return data;
}

/** A plane that places the sketch in 3D. */
export function isPlaced(plane: SketchPlane | null | undefined): boolean {
  return !!plane && (plane.state === 'CONFIRMED' || plane.state === 'ATTACHED');
}
