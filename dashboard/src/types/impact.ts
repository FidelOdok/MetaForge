/**
 * Interface edges and cross-domain impact (FORGE-793).
 *
 * Mirrors `twin_core/models/interface.py` and `twin_core/impact/cross_domain.py`.
 * This is product dependency (a board mounted to an enclosure, a pin map used
 * by firmware), not the workflow-step DAG.
 */

export type InterfaceType =
  | 'mounts_to'
  | 'clearance_to'
  | 'connects_via'
  | 'pin_maps_to'
  | 'simulation_input_of'
  | 'feature_of'
  | 'manufactured_by';

export type InterfaceStatus = 'proposed' | 'confirmed' | 'removed';
export type DeclaredBy = 'human' | 'tool' | 'inferred';

export interface InterfaceEnd {
  ref: string;
  revision: number | null;
  domain: string;
  item_type: string;
  node_id: string | null;
  label: string;
}

export interface InterfaceEdge {
  id: string;
  project_id: string;
  type: InterfaceType;
  source: InterfaceEnd;
  target: InterfaceEnd;
  declared_by: DeclaredBy;
  confidence: number;
  status: InterfaceStatus;
  coupled_parameters: string[];
  rationale: string;
  evidence: Record<string, unknown>;
  derived: boolean;
  domain_pair: [string, string];
  symmetric: boolean;
  source_behind?: boolean;
  target_behind?: boolean;
}

export interface GraphItem {
  ref: string;
  label: string;
  domain: string;
  item_type: string;
  head_revision: number | null;
}

export interface InterfaceListResponse {
  project_id: string;
  edges: InterfaceEdge[];
  nodes: GraphItem[];
}

/** The shared validation status vocabulary (CHG-005). */
export type ValidationStatus = 'PASS' | 'WARN' | 'FAIL' | 'INCOMPLETE' | 'NOT_EVALUATED' | 'STALE';

export interface Coupling {
  edge_id: string;
  edge_type: InterfaceType;
  edge_status: InterfaceStatus;
  rationale: string;
}

export interface ImpactedEntity {
  ref: string;
  label: string;
  domain: string;
  item_type: string;
  revision: number | null;
  depth: number;
  direct: boolean;
  edge_id: string;
  edge_type: InterfaceType;
  edge_status: InterfaceStatus;
  via: string[];
  rationale: string;
  proposed_action: string;
  parameters: string[];
  modules: string[];
  certain: boolean;
  couplings: Coupling[];
}

export interface StaleResult {
  record_id: string;
  record_type: string;
  check_kind: string;
  name: string;
  pinned_ref: string;
  current_ref: string | null;
  belongs_to: string | null;
  input_revisions: string[];
  previous_status: ValidationStatus;
  status: ValidationStatus;
  reason: string;
  tool: string | null;
  marked: boolean;
}

export type RerunStatus = 'proposed' | 'queued' | 'approved' | 'rejected';

export interface ProposedRerun {
  id: string;
  tool: string;
  check_kind: string;
  target_ref: string;
  target_domain: string;
  label: string;
  reason: string;
  consequential: boolean;
  disposition: 'requires_approval' | 'may_run_unattended';
  disposition_reason: string;
  status: RerunStatus;
  record_id: string | null;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
}

export interface ReviewAction {
  kind: string;
  target_ref: string;
  label: string;
  reason: string;
}

export interface NotAffected {
  ref: string;
  label: string;
  domain: string;
  reason: string;
}

export interface ImpactGraphNode {
  ref: string;
  label: string;
  domain: string;
  role: 'changed' | 'impacted' | 'possible' | 'unaffected' | 'record';
}

export interface ImpactGraphLink {
  source: string;
  target: string;
  type: InterfaceType;
  status: InterfaceStatus;
  impacted: boolean;
}

export interface ImpactResult {
  id: string | null;
  project_id: string;
  preview: boolean;
  change: {
    item_key: string;
    old_revision: number | null;
    new_revision: number | null;
    changed_parameters: string[] | null;
  };
  source_domain: string;
  source_label: string;
  impacted: ImpactedEntity[];
  by_domain: Record<string, string[]>;
  stale_results: StaleResult[];
  proposed_reruns: ProposedRerun[];
  review_actions: ReviewAction[];
  not_affected: NotAffected[];
  cycles: string[][];
  depth_limited: boolean;
  max_depth: number;
  summary: string;
  next_action: string;
  graph: { nodes: ImpactGraphNode[]; links: ImpactGraphLink[] };
  approval_policy: Record<string, unknown>;
  operation_id: string | null;
  created_at: string | null;
  transaction?: Record<string, unknown>;
}

/** The approval id of a rerun that waits for a person. */
export function rerunApprovalId(impactId: string, rerunId: string): string {
  return `rerun:${impactId}:${rerunId}`;
}

/** The impact id an operation id names (`job:impact-<id>`), else null. */
export function impactIdOf(operationId: string): string | null {
  const prefix = 'job:impact-';
  return operationId.startsWith(prefix) ? operationId.slice(prefix.length) || null : null;
}
