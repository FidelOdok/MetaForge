import apiClient from '../client';

/**
 * Make a 3D concept from the Create stage (3D-RC-001, L0).
 *
 * The gateway starts `twin.generate_concept_mesh` as a background job from
 * the accepted generated concept, else the newest concept image, else the
 * brief in words. Its progress arrives as operation events on the project
 * stream. The result is an illustrative GLB, never CAD.
 */

const enc = encodeURIComponent;
const SURFACE_HEADERS = { 'X-MetaForge-Surface': 'dashboard' } as const;

export const CONCEPT_3D_LABEL = '3D Concept · illustrative';

export type ConceptMeshSourceKind = 'generated_sketch' | 'concept_image' | 'text';

export interface ConceptMeshSource {
  kind: ConceptMeshSourceKind;
  node_id: string | null;
  item_ref: string | null;
  name: string | null;
  prompt: string | null;
}

export interface ConceptMeshSpend {
  used_last_hour: number;
  cap: number | null;
  policy_mode: string;
  policy_would_approve: boolean;
}

export interface ConceptMeshStarted {
  job_id: string;
  status: string;
  operation_id: string;
  reused: boolean;
  expected_seconds: number | null;
  source: ConceptMeshSource;
  spend: ConceptMeshSpend;
  result: Record<string, unknown> | null;
  error: string | null;
  authority: string;
  label: string;
}

export interface ConceptMeshSummary {
  node_id: string;
  name: string;
  created_at: string;
  authority: string;
  label: string;
  source_kind?: ConceptMeshSourceKind;
  /** A short manufacturing note, when one was assessed (else "Manufacturing not assessed"). */
  manufacturing_note?: string | null;
  fit_to_envelope?: boolean;
  scale_factor?: number;
}

export interface ConceptMeshState {
  latest: ConceptMeshSummary | null;
  active_job: { job_id: string; status: string } | null;
  next_source: ConceptMeshSource | null;
  spend: ConceptMeshSpend;
}

export interface ConceptMeshBody {
  source?: 'auto' | ConceptMeshSourceKind;
  image_node_id?: string | null;
  prompt?: string | null;
  confirm_spend?: boolean;
}

const base = (projectId: string) => `/twin/projects/${enc(projectId)}/concept-mesh`;

export async function startConceptMesh(projectId: string, body: ConceptMeshBody = {}): Promise<ConceptMeshStarted> {
  const { data } = await apiClient.post<ConceptMeshStarted>(base(projectId), body, { headers: SURFACE_HEADERS });
  return data;
}

export async function getConceptMesh(projectId: string, signal?: AbortSignal): Promise<ConceptMeshState> {
  const { data } = await apiClient.get<ConceptMeshState>(base(projectId), { signal });
  return data;
}
