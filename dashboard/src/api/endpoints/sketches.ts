import apiClient from '../client';
import type { SketchStroke } from '../../store/sketch-store';

/**
 * Freehand sketches drawn on the Create stage (FORGE-789).
 *
 * A save is the whole sketch; each becomes the next revision of the
 * project's `sketch_strokes` item. A retried `save_id` returns the revision
 * it already made. These calls never go through the chat send queue.
 */

const enc = encodeURIComponent;

export interface SketchSaveBody {
  save_id: string;
  name?: string;
  item_key?: string | null;
  base_revision?: string | null;
  strokes: SketchStroke[];
  viewport?: { width_px: number; height_px: number } | null;
  reference_node_id?: string | null;
  plane_state: 'UNASSIGNED';
  scale: 'unknown';
}

export interface SketchSaveResult {
  node_id: string;
  item_key: string | null;
  revision: number | null;
  item_ref: string | null;
  parent_revision: string | null;
  save_id: string;
  operation_id: string;
  duplicate: boolean;
  stroke_count: number;
  authority: 'human_sketch';
  scale: 'unknown';
  plane_state: 'UNASSIGNED';
  content_hash: string;
}

export interface SketchRevision {
  node_id: string;
  item_key: string | null;
  revision: number | null;
  item_ref: string | null;
  parent_revision: string | null;
  name: string;
  save_id: string | null;
  strokes: SketchStroke[];
  viewport: { width_px: number; height_px: number } | null;
  reference_node_id: string | null;
  authority: string;
  scale: string;
  plane_state: string;
  created_at: string | null;
}

/** `POST /v1/twin/projects/{id}/sketches`. */
export async function saveSketch(projectId: string, body: SketchSaveBody): Promise<SketchSaveResult> {
  const { data } = await apiClient.post<SketchSaveResult>(`/twin/projects/${enc(projectId)}/sketches`, body);
  return data;
}

/** `GET /v1/twin/projects/{id}/sketches/current`; `null` when none is saved yet. */
export async function getCurrentSketch(projectId: string, signal?: AbortSignal, itemKey?: string | null): Promise<SketchRevision | null> {
  try {
    const { data } = await apiClient.get<SketchRevision>(`/twin/projects/${enc(projectId)}/sketches/current`, {
      signal,
      ...(itemKey ? { params: { item_key: itemKey } } : {}),
    });
    return data;
  } catch (err) {
    const status = (err as { response?: { status?: number } }).response?.status;
    if (status === 404) return null;
    throw err;
  }
}
