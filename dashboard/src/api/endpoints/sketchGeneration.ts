import apiClient from '../client';
import type { Point } from '../../components/sketch/geometry';

/**
 * AI concept sketch generation on the Create stage (FORGE-794).
 *
 * A request starts a job on its generation target; its progress arrives as
 * operation events on the project stream (queued, started, completed), so no
 * call here waits for an image. Each output is a proposal: it never replaces
 * the accepted concept until the person accepts it. Every image is
 * illustrative: never an editable sketch and never CAD.
 */

const enc = encodeURIComponent;
const SURFACE_HEADERS = { 'X-MetaForge-Surface': 'dashboard' } as const;

export const SKETCH_STYLES = ['industrial_sketch', 'marker_render', 'line_art', 'concept_drawing'] as const;
export type SketchStyle = (typeof SKETCH_STYLES)[number];

export const SKETCH_STYLE_LABELS: Record<SketchStyle, string> = {
  industrial_sketch: 'Industrial sketch',
  marker_render: 'Marker render',
  line_art: 'Line art',
  concept_drawing: 'Concept drawing',
};

export type GenerationMode = 'text' | 'reference' | 'sketch';

export const GENERATED_LABEL = 'Generated concept · illustrative';

export interface SketchRegion {
  polygon: Point[];
  label?: string;
}

export interface GenerateSketchBody {
  intent: string;
  style: SketchStyle;
  constraints?: string[];
  negative?: string[];
  protected_features?: string[];
  reference_ids?: string[];
  sketch_key?: string | null;
  sketch_revision?: string | null;
  mask?: SketchRegion | null;
  mask_instruction?: string;
  locked_regions?: SketchRegion[];
  n_variants?: number;
  baseline_key?: string | null;
  expected_baseline?: string | null;
  confirm_spend?: boolean;
}

export interface GenerationStarted {
  job_id: string;
  status: string;
  operation_id: string;
  mode: GenerationMode;
  target_key: string;
  base_revision: string;
  reused: boolean;
  expected_seconds: number | null;
  authority: 'illustrative';
}

export interface Preservation {
  method: 'provider_mask' | 'composited' | 'none';
  score: number | null;
  passed: boolean;
  failures: string[];
  outside_mask?: number | null;
  outside_mask_raw?: number | null;
  threshold: number;
}

export interface GeneratedConcept {
  node_id: string;
  name: string;
  generation_status: 'proposed' | 'accepted' | 'rejected' | 'stale' | null;
  review_required: boolean;
  generation_id: string | null;
  variant_index: number | null;
  item_key: string | null;
  item_ref: string | null;
  accepted_as: string | null;
  baseline_for: string | null;
  mode: GenerationMode | null;
  style: SketchStyle | null;
  intent: string | null;
  provider: string | null;
  model: string | null;
  base_revision: string | null;
  preservation: Preservation | null;
  latency_s: number | null;
  /** The image size, e.g. `1024x1024`; its shorter side spans one sketch unit. */
  size: string | null;
  stale_reason: string | null;
  created_at: string | null;
  authority: 'illustrative';
  label: string;
  editable: false;
}

export interface ConceptLine {
  key: string;
  baseline_for: string | null;
  head_ref: string;
  head: GeneratedConcept | null;
  history: GeneratedConcept[];
}

export interface GenerationOverview {
  lines: ConceptLine[];
  proposals: GeneratedConcept[];
}

export interface Comparison {
  a: GeneratedConcept;
  b: GeneratedConcept;
  pixel_difference: number | null;
  changed: string[];
}

const base = (projectId: string) => `/twin/projects/${enc(projectId)}/sketch-generations`;

export async function startGeneration(projectId: string, body: GenerateSketchBody): Promise<GenerationStarted> {
  const { data } = await apiClient.post<GenerationStarted>(base(projectId), body, { headers: SURFACE_HEADERS });
  return data;
}

export async function listGenerations(projectId: string, signal?: AbortSignal): Promise<GenerationOverview> {
  const { data } = await apiClient.get<GenerationOverview>(base(projectId), { signal });
  return data;
}

export async function acceptGeneration(projectId: string, nodeId: string, expectedBaseline: string | null) {
  const { data } = await apiClient.post<{ accepted: string; node_id: string; previous_baseline: string | null }>(
    `${base(projectId)}/${enc(nodeId)}/accept`,
    { expected_baseline: expectedBaseline },
    { headers: SURFACE_HEADERS },
  );
  return data;
}

export async function rejectGeneration(projectId: string, nodeId: string) {
  const { data } = await apiClient.post(`${base(projectId)}/${enc(nodeId)}/reject`, {}, { headers: SURFACE_HEADERS });
  return data;
}

export async function branchGeneration(projectId: string, nodeId: string) {
  const { data } = await apiClient.post<{ branch_key: string; item_ref: string }>(
    `${base(projectId)}/${enc(nodeId)}/branch`,
    {},
    { headers: SURFACE_HEADERS },
  );
  return data;
}

export async function compareGenerations(projectId: string, a: string, b: string): Promise<Comparison> {
  const { data } = await apiClient.get<Comparison>(`${base(projectId)}/compare`, { params: { a, b } });
  return data;
}

/** The gateway asks a person to confirm before going past the hourly image cap. */
export function spendConfirmationOf(error: unknown): string | null {
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } })?.response;
  const detail = response?.data?.detail as { code?: string; message?: string } | undefined;
  if (response?.status === 409 && detail && typeof detail === 'object' && detail.code === 'spend_confirmation_required') {
    return detail.message ?? 'The hourly image limit is reached.';
  }
  return null;
}
