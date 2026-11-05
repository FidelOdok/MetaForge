import apiClient from '../client';

/**
 * Concept parts (L0): split the 3D concept into parts, confirm which component
 * each part is, then redo or seal one part. The paid steps run as gateway jobs
 * whose progress arrives as operation events; past the hourly cap the gateway
 * answers 409 `spend_confirmation_required`. Every result is a proposed
 * revision: the accepted concept stays until the person accepts the new one.
 */

const enc = encodeURIComponent;
const SURFACE_HEADERS = { 'X-MetaForge-Surface': 'dashboard' } as const;

export type MappingState = 'proposed' | 'question' | 'confirmed' | 'not_a_component';

export interface PartCandidate {
  component_id: string | null;
  name: string;
  score?: number;
}

export interface MappedPart {
  part: string;
  node_index: number | null;
  label: string | null;
  proposed_component_id: string | null;
  proposed_name: string | null;
  confidence: number;
  basis: string[];
  candidates: PartCandidate[];
  state: MappingState;
  confirmed_component_id?: string | null;
  confirmed_name?: string | null;
  confirmed_by?: string;
}

export interface PartMapping {
  status: 'proposed' | 'partially_confirmed' | 'confirmed' | 'not_segmented';
  transform?: string | null;
  method?: string;
  parts: MappedPart[];
  questions: { part: string; question: string; options: PartCandidate[] }[];
  unmatched_components: { component_id: string; name: string }[];
}

export interface Segmentation {
  provider: string;
  model_version: string;
  granularity: string | null;
  task_id: string;
  input_kind: string;
  credits: number | null;
  status: 'segmented' | 'not_segmented';
  note: string;
  labelled: boolean;
  part_count: number;
}

export interface ConceptRevision {
  node_id: string;
  name: string;
  created_at: string;
  generated_by: string;
  concept_revision: number;
  concept_status: 'accepted' | 'proposed' | 'superseded' | 'rejected';
  operation: string;
  lineage: Record<string, unknown> | null;
  segmentation: Segmentation | null;
  mapping: PartMapping | null;
  regenerated_parts: Record<string, unknown>[];
  completed_parts: Record<string, unknown>[];
  authority: string;
  label: string | null;
}

export interface Spend {
  used_last_hour: number;
  cap: number | null;
  policy_mode: string;
  policy_would_approve: boolean;
}

export interface ConceptPartsState {
  current: ConceptRevision | null;
  proposals: ConceptRevision[];
  components: { component_id: string; name: string }[];
  active_jobs: { job_id: string; status: string; tool_id?: string }[];
  spend: Spend;
}

export interface Started {
  job_id: string;
  status: string;
  operation_id: string;
  tool: string;
  spend: Spend;
  error: string | null;
}

export const conceptPartsKeys = {
  project: (projectId: string) => ['concept-parts', projectId] as const,
};

const base = (projectId: string) => `/twin/projects/${enc(projectId)}/concept-parts`;

export async function getConceptParts(projectId: string, signal?: AbortSignal): Promise<ConceptPartsState> {
  const { data } = await apiClient.get<ConceptPartsState>(base(projectId), { signal });
  return data;
}

export async function segmentConcept(
  projectId: string,
  body: { node_id?: string; confirm_spend?: boolean } = {},
): Promise<Started> {
  const { data } = await apiClient.post<Started>(`${base(projectId)}/segment`, body, { headers: SURFACE_HEADERS });
  return data;
}

export async function confirmMapping(
  projectId: string,
  nodeId: string,
  body: { assignments: { part: string; component_id: string | null }[]; accept_proposals?: boolean },
): Promise<ConceptRevision> {
  const { data } = await apiClient.post<ConceptRevision>(`${base(projectId)}/${enc(nodeId)}/mapping`, body, {
    headers: SURFACE_HEADERS,
  });
  return data;
}

export interface RegenerateBody {
  component_id: string;
  source: 'text' | 'image';
  prompt?: string;
  image_base64?: string;
  image_mime?: string;
  confirm_spend?: boolean;
}

export async function regeneratePart(projectId: string, nodeId: string, body: RegenerateBody): Promise<Started> {
  const { data } = await apiClient.post<Started>(`${base(projectId)}/${enc(nodeId)}/regenerate`, body, {
    headers: SURFACE_HEADERS,
  });
  return data;
}

export async function completePart(
  projectId: string,
  nodeId: string,
  body: { component_id: string; completion_mode: 'quick_cap' | 'ai_completion'; confirm_spend?: boolean },
): Promise<Started> {
  const { data } = await apiClient.post<Started>(`${base(projectId)}/${enc(nodeId)}/complete`, body, {
    headers: SURFACE_HEADERS,
  });
  return data;
}

export async function decideRevision(projectId: string, nodeId: string, decision: 'accept' | 'reject'): Promise<ConceptRevision> {
  const { data } = await apiClient.post<ConceptRevision>(
    `${base(projectId)}/${enc(nodeId)}/decision`,
    { decision },
    { headers: SURFACE_HEADERS },
  );
  return data;
}
