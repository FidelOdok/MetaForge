import apiClient from '../client';

/**
 * Parametric reconstruction (spec 4B, api_gateway/twin/reconstruction_routes.py).
 *
 * An accepted concept becomes a proposed feature history per component: a
 * sketch profile, an extrude or revolve, a shell, openings and bosses, each
 * parameter estimated, confirmed or unknown with its source, the open
 * questions, and the DFM strategy note naming the rules that shaped it.
 * Accepting features makes parametric sketches held for approval.
 */

const enc = encodeURIComponent;

export type ParamStatus = 'estimated' | 'confirmed' | 'unknown';

export interface Provenance {
  kind: string;
  ref: string | null;
  detail: string;
}

export interface ProposedParameter {
  value: number | string | null;
  unit: string | null;
  status: ParamStatus;
  source: Provenance | null;
  question: string;
  minimum: number | null;
  minimum_rule: string | null;
}

export interface ProposedFeature {
  id: string;
  kind: 'sketch_profile' | 'extrude' | 'revolve' | 'loft' | 'shell' | 'opening' | 'boss' | 'rib';
  name: string;
  plain: string;
  depends_on: string[];
  parameters: Record<string, ProposedParameter>;
  placement: { plane: string; offset: number; status: ParamStatus; source: Provenance | null } | null;
  confidence: number | null;
  suggested_by: 'deterministic' | 'llm';
  provenance: Provenance[];
  dfm_rules: string[];
  buildable: boolean;
  existing_ref: string | null;
  status: 'proposed' | 'accepted' | 'rejected';
  note: string;
}

export interface Ambiguity {
  id: string;
  question: string;
  options: { id: string; label: string }[];
  feature_id: string | null;
  consequential: boolean;
  default: string | null;
  choice: string | null;
}

export interface FeatureHistoryProposal {
  component_id: string;
  component_name: string;
  status: 'proposed' | 'accepted' | 'partially_accepted';
  features: ProposedFeature[];
  assumptions: { id: string; text: string; feature_id: string | null }[];
  ambiguities: Ambiguity[];
  dfm_strategy: {
    profile_id: string | null;
    profile_version: string | null;
    process: string | null;
    process_authority: string;
    context_ref: string | null;
    rules: { rule_id: string; title: string; kind: string; limit: number | null; unit: string; source: string; applied_as: string }[];
    text: string;
  };
  summary: string;
  inputs: {
    assembly_decision_id: string | null;
    layout_ref: string | null;
    layout_node_id: string | null;
    mesh_node_id: string | null;
    reference_ids: string[];
    sketch_refs: string[];
    context_ref: string | null;
  };
  llm: { requested: boolean; outcome: string; provider: string | null; model: string | null; suggestions: number; note: string };
  accepted_sketches: Record<string, string>;
}

export interface ProposalView {
  component_id: string;
  component_name: string;
  item_key: string;
  revision: number | null;
  item_ref: string | null;
  node_id: string;
  status: string;
  staleness: string;
  open_questions: number;
  proposal: FeatureHistoryProposal;
}

export interface ReconstructionView {
  project_id: string;
  concept_accepted: boolean;
  assembly_decision_id: string | null;
  components: { component_id: string; name: string; source: string; proposal: ProposalView | null }[];
  summary: string;
  accepted: boolean;
}

export interface ProposeReply {
  project_id: string;
  operation_id: string;
  proposals: ProposalView[];
  reused: string[];
  skipped: { component_id: string; name: string; reason: string }[];
  summary: string;
}

export interface AcceptBody {
  feature_ids: string[];
  edits?: Record<string, Record<string, number | string>>;
  choices?: Record<string, string>;
  base_revision?: number | null;
}

export interface AcceptReply {
  project_id: string;
  proposal: ProposalView;
  accepted: string[];
  rejected: string[];
  sketches: { feature_id: string; item_ref: string | null; node_id: string; approval_id: string; required_input: string[] }[];
  existing: Record<string, string>;
  process_written: string | null;
}

export const reconstructionKeys = {
  all: ['reconstruction'] as const,
  project: (projectId: string) => ['reconstruction', projectId] as const,
};

/** `GET /v1/twin/projects/{id}/reconstruction`: the latest proposal per component. */
export async function getReconstruction(projectId: string, signal?: AbortSignal): Promise<ReconstructionView> {
  const { data } = await apiClient.get<ReconstructionView>(`/twin/projects/${enc(projectId)}/reconstruction`, { signal });
  return data;
}

/** `POST /v1/twin/projects/{id}/reconstruction`: propose (or reuse) feature histories. */
export async function proposeReconstruction(
  projectId: string,
  body: { component_ids?: string[]; use_llm?: boolean } = {},
): Promise<ProposeReply> {
  const { data } = await apiClient.post<ProposeReply>(`/twin/projects/${enc(projectId)}/reconstruction`, body);
  return data;
}

/** `POST /v1/twin/projects/{id}/reconstruction/{key}/accept`. */
export async function acceptReconstruction(projectId: string, key: string, body: AcceptBody): Promise<AcceptReply> {
  const { data } = await apiClient.post<AcceptReply>(
    `/twin/projects/${enc(projectId)}/reconstruction/${enc(key)}/accept`,
    body,
  );
  return data;
}

/** The questions still open on a proposal. */
export function openQuestions(p: FeatureHistoryProposal): Ambiguity[] {
  return p.ambiguities.filter((a) => !a.choice && a.options.length > 0);
}

/** The features Create accepts on the person's behalf: everything the concept says, not model suggestions. */
export function defaultFeatureIds(p: FeatureHistoryProposal): string[] {
  return p.features.filter((f) => f.suggested_by === 'deterministic' && f.status === 'proposed').map((f) => f.id);
}
