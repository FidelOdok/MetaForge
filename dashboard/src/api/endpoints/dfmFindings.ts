import apiClient from '../client';

/**
 * Heuristic DFM findings (C3D slice 4, api_gateway/twin/dfm_routes.py).
 *
 * Preliminary manufacturing guidance per component: anchored findings
 * (component, feature or face), measured against a limit with its source,
 * what was not assessed and why, and a status that is never a pass.
 */

const enc = encodeURIComponent;

export type DfmStatus = 'not_assessed' | 'preliminary_guidance' | 'analysis_pending' | 'issues_found';

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
};

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
