import apiClient from '../client';
import { resolveGatewayHref } from '../../lib/gatewayConfig';

/**
 * DFM findings (C3D slices 4 and 6, api_gateway/twin/dfm_routes.py).
 *
 * Per component: heuristic guidance (never a pass) or the exact checks on a
 * validated model, with anchored findings (component, feature or B-Rep
 * faces), measured against a limit with its source, what was not assessed
 * and why, and physical verification items. Plus the revision-specific DFM
 * report and the manufacturing release checklist.
 */

const enc = encodeURIComponent;

export type DfmStatus =
  | 'not_assessed'
  | 'preliminary_guidance'
  | 'analysis_pending'
  | 'issues_found'
  | 'analysis_passed'
  | 'verification_required'
  | 'released';

export type DfmDecisionKind = 'keep_design' | 'try_fix' | 'explore_process';

export interface DfmFinding {
  id: string;
  rule_id: string;
  rule_title: string;
  profile_id: string;
  profile_version: string;
  process: string;
  rule_kind: 'machine_limit' | 'supplier_limit' | 'heuristic' | 'general_guidance';
  severity: 'blocking' | 'advisory';
  anchor: { component_id: string; feature: string | null; face_signature: string | null; faces: string[] };
  quantity: string;
  measured: number | string | null;
  limit: number | string | null;
  unit: string;
  comparison: string;
  confidence: 'exact' | 'human_confirmed' | 'design_parameter' | 'estimated';
  limit_source: string;
  evidence_refs: string[];
  message: string;
  suggested_fix: string;
  fix_changes: string[];
  decision?: { decision: DfmDecisionKind; by?: string; at?: string; reason?: string } | null;
}

export interface DfmNotAssessed {
  rule_id: string;
  title: string;
  reason: string;
  text: string;
}

export interface DfmVerificationEvidence {
  id: string;
  note: string;
  attachment: { name: string; size_bytes: number; sha256: string; object_key: string | null } | null;
  recorded_by: string;
  recorded_at: string;
  geometry_ref: string;
  state: 'awaiting_approval' | 'approved' | 'outdated';
  approval_id: string;
}

export interface DfmVerificationItem {
  id: string;
  rule_id: string;
  component_id: string;
  title: string;
  kind: 'physical_test' | 'supplier_confirmation';
  needed: string;
  reason: string;
  source: string;
  status: 'open' | 'awaiting_approval' | 'satisfied' | 'rejected';
  evidence: DfmVerificationEvidence[];
  satisfied_by: { by: string; at: string; approval_id: string; geometry_ref: string } | null;
}

export interface DfmComponentFindings {
  component_id: string;
  name: string;
  finding_set_id: string;
  status: DfmStatus;
  status_label: string;
  note: string;
  process: string | null;
  process_authority: string;
  profile: { id: string; version: string } | null;
  geometry_level: string;
  geometry_refs: string[];
  context_ref: string;
  staleness: { status: string; reason?: string | null } & Record<string, unknown>;
  findings: DfmFinding[];
  not_assessed: DfmNotAssessed[];
  rules_run: string[];
  evaluated_at: string;
  trigger: string;
  /** `heuristic` guidance, or `exact` checks on a validated model (slice 6). */
  kind?: 'heuristic' | 'exact';
  exact_checks_scheduled?: boolean;
  checks_required?: string[];
  complete?: boolean;
  guidance_only_rules?: string[];
  exact_evidence?: Record<string, unknown>;
  verification_items?: DfmVerificationItem[];
}

export interface DfmView {
  project_id: string;
  status: DfmStatus;
  status_label: string;
  note: string;
  suggestions: number;
  blocking: number;
  components: DfmComponentFindings[];
  history: DfmComponentFindings[];
  profiles: { id: string; version: string; process: string }[];
  heuristic_only: boolean;
  verification_open?: number;
  disclaimer: string;
}

export interface DfmAlternative {
  process: string;
  profile: { id: string; version: string };
  status: DfmStatus;
  note: string;
  findings: number;
  blocking: number;
  not_assessed: number;
  preview: boolean;
}

export interface DfmDecisionResult {
  finding_id: string;
  decision: DfmDecisionKind;
  finding_set_id: string;
  component_id: string;
  applied: boolean;
  message: string;
  requires_approval?: boolean;
  approval_id?: string;
  protected?: string[];
  proposal?: { suggested_fix: string; changes: string[] } & Record<string, unknown>;
  alternatives?: DfmAlternative[];
}

export const dfmKeys = {
  project: (projectId: string, history = false) => ['dfm', projectId, history] as const,
  checklist: (projectId: string) => ['dfm', projectId, 'checklist'] as const,
};

export type ChecklistItemId = 'components_exact' | 'dfm' | 'verification' | 'approvals' | 'step_round_trip';

export interface ChecklistItem {
  id: ChecklistItemId;
  label: string;
  status: 'pass' | 'fail' | 'not_applicable';
  detail: string;
  blockers: string[];
  next_steps: string[];
}

/** What Create may claim: never more than the checklist backs (section 16.2 item 7). */
export interface Readiness {
  level: 'not_assessed' | 'in_progress' | 'prototype' | 'production';
  label: string;
  prototype: boolean;
  production: boolean;
  next_steps: string[];
}

export interface ReleaseChecklist {
  project_id: string;
  applies: boolean;
  releasable?: boolean;
  blockers?: string[];
  items?: ChecklistItem[];
  revision_refs?: string[];
  readiness: Readiness;
}

export interface DfmReportResponse {
  report_id: string;
  generated_at: string | null;
  generated_by: string | null;
  staleness: { status: string; reason?: string | null } & Record<string, unknown>;
  report: { status: string; status_label: string; product_revision: { refs: string[]; key: string } } & Record<
    string,
    unknown
  >;
  markdown: string;
  reused?: boolean;
}

export async function getReleaseChecklist(projectId: string, signal?: AbortSignal): Promise<ReleaseChecklist> {
  const response = await apiClient.get<ReleaseChecklist>(`/twin/projects/${enc(projectId)}/dfm/checklist`, { signal });
  return response.data;
}

export async function generateDfmReport(projectId: string): Promise<DfmReportResponse> {
  const response = await apiClient.post<DfmReportResponse>(`/twin/projects/${enc(projectId)}/dfm/report`);
  return response.data;
}

/** The latest report's rendered Markdown, as a gateway path for a link. */
export function dfmReportMarkdownPath(projectId: string): string {
  return resolveGatewayHref(`/v1/twin/projects/${enc(projectId)}/dfm/report?format=markdown`);
}

export async function recordVerificationEvidence(
  projectId: string,
  itemId: string,
  body: { note: string; attachment_name?: string | null; attachment_base64?: string | null },
): Promise<{ approval_id: string; requires_approval: boolean; message: string }> {
  const response = await apiClient.post(`/twin/projects/${enc(projectId)}/dfm/verification/${enc(itemId)}/evidence`, body);
  return response.data;
}

/** `CAD-ENCLOSURE@2` -> `{ itemKey: 'CAD-ENCLOSURE', revision: 2 }`; null for anything else. */
export function itemRef(ref: string | undefined): { itemKey: string; revision: number } | null {
  const m = /^([A-Z][A-Z0-9_-]*)@(\d+)$/.exec(ref ?? '');
  return m && m[1] && m[2] ? { itemKey: m[1], revision: Number(m[2]) } : null;
}

export async function getDfm(projectId: string, opts: { history?: boolean; signal?: AbortSignal } = {}): Promise<DfmView> {
  const response = await apiClient.get<DfmView>(`/twin/projects/${enc(projectId)}/dfm`, {
    params: opts.history ? { history: true } : undefined,
    signal: opts.signal,
  });
  return response.data;
}

export async function evaluateDfm(projectId: string, componentIds?: string[]): Promise<DfmView> {
  const response = await apiClient.post<DfmView>(`/twin/projects/${enc(projectId)}/dfm/evaluate`, {
    component_ids: componentIds ?? null,
  });
  return response.data;
}

export async function decideDfmFinding(
  projectId: string,
  findingId: string,
  decision: DfmDecisionKind,
  reason = '',
): Promise<DfmDecisionResult> {
  const response = await apiClient.post<DfmDecisionResult>(
    `/twin/projects/${enc(projectId)}/dfm/findings/${enc(findingId)}/decision`,
    { decision, reason },
  );
  return response.data;
}

/** Findings no person has decided to keep the design for. */
export function openFindings(view: DfmView | undefined): { component: DfmComponentFindings; finding: DfmFinding }[] {
  if (!view) return [];
  return view.components.flatMap((component) =>
    component.findings
      .filter((finding) => finding.decision?.decision !== 'keep_design')
      .map((finding) => ({ component, finding })),
  );
}
