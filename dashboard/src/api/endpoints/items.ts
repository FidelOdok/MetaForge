import apiClient from '../client';

/* FORGE-526: items, baselines and the project's current view.
 * Shapes mirror api_gateway/twin/baseline_routes.py and item_routes.py
 * (snake_case, as the gateway returns them). */

export interface DraftRevision {
  revision: number;
  node_id: string;
  status: string;
  name: string | null;
  run_id: string | null;
  change_reason: string | null;
  created_at: string | null;
  change_set?: string | null;
}

export interface CurrentItemRow {
  key: string;
  item_type: string;
  name: string;
  revision: number | null;
  ref: string | null;
  node_id: string | null;
  revision_status: string;
  validation_status: string;
  run_id: string | null;
  gate_id: string | null;
  change_reason: string | null;
  author: string | null;
  updated_at: string | null;
  revision_count: number;
  evidence_state: 'current' | 'out_of_date' | 'none';
  evidence_count: number;
  drafts: DraftRevision[];
}

export interface AnalysedRef {
  key: string;
  revision: number;
  ref: string;
  current: boolean;
}

export interface RecordRow {
  node_id: string;
  record_type: 'design_decision' | 'simulation_result' | 'evidence';
  name: string;
  created_at: string | null;
  analysed: AnalysedRef[];
  out_of_date: boolean;
  /** FORGE-527 recorded status: current, stale, invalid, superseded, revalidated. */
  staleness?: string | null;
}

export interface OtherRow {
  node_id: string;
  name: string;
  type: string;
  validation_status: string;
  updated_at: string | null;
}

export interface BaselineSummary {
  id: string;
  name: string;
  project_id: string | null;
  created_at: string;
  approved_by: string[];
  reason: string;
  gate_id: string | null;
  run_id: string | null;
  source: 'gate' | 'manual';
  item_count: number;
  member_count: number;
}

export interface BaselineItemRef {
  key: string;
  item_type: string;
  revision: number;
  ref: string;
  node_id: string;
  name: string;
}

export interface BaselineDetail extends BaselineSummary {
  items: BaselineItemRef[];
}

export interface CurrentView {
  project_id: string | null;
  items: CurrentItemRow[];
  groups: { item_type: string; count: number; keys: string[] }[];
  records: RecordRow[];
  other: OtherRow[];
  counts: {
    items: number;
    other: number;
    total: number;
    valid: number;
    warning: number;
    error: number;
    unknown: number;
    drafts: number;
    superseded_revisions: number;
    decisions: number;
    simulation_results: number;
    evidence: number;
    out_of_date_results: number;
    by_type: Record<string, number>;
  };
  readiness: number;
  latest_baseline: BaselineSummary | null;
}

export interface ItemRevision {
  revision: number;
  node_id: string;
  name: string | null;
  change_reason: string | null;
  run_id: string | null;
  author: string | null;
  created_at: string | null;
  is_head: boolean;
  adopted: boolean;
  status: string;
  change_set: string | null;
  phase: string | null;
  gate: string | null;
  status_reason: string | null;
  baselines: string[];
}

export interface ItemHistory {
  item: {
    key: string;
    item_type: string;
    name: string;
    project_id: string | null;
    head_revision: number;
    head_node_id: string | null;
    head_ref: string | null;
  };
  revisions: ItemRevision[];
  current: { revision: number; node_id: string; ref: string } | null;
}

export interface FieldChange {
  name: string;
  status: 'changed' | 'added' | 'removed';
  from: unknown;
  to: unknown;
}

export interface RequirementChange extends FieldChange {
  from_severity: string | null;
  to_severity: string | null;
  unit: string;
}

export interface GeometrySide {
  volume_mm3: number | null;
  bounding_box: Record<string, number> | null;
  mass_kg: number | null;
}

export interface ItemDiff {
  key: string;
  item_type: string;
  name: string;
  a: { revision: number; node_id: string; status: string; name: string | null };
  b: { revision: number; node_id: string; status: string; name: string | null };
  a_ref: string;
  b_ref: string;
  geometry: {
    available: boolean;
    source: 'describe_step_file' | 'recorded' | null;
    a: GeometrySide;
    b: GeometrySide;
    volume_delta_mm3: number | null;
    mass_delta_kg: number | null;
    mass_source: string | null;
    bounding_box_delta: Record<string, number> | null;
  } | null;
  parameters: FieldChange[];
  requirements: RequirementChange[];
  fields: FieldChange[];
  dependents: { node_id: string; name: string; type: string; via: string }[];
  warnings: string[];
}

export interface BaselineItemDiff {
  key: string;
  item_type: string;
  name: string;
  status: 'unchanged' | 'changed' | 'added' | 'removed';
  from_ref: string | null;
  to_ref: string | null;
}

export interface BaselineDiff {
  a: BaselineSummary;
  b: BaselineSummary | null;
  b_is_current: boolean;
  items: BaselineItemDiff[];
  counts: Record<'unchanged' | 'changed' | 'added' | 'removed', number>;
}

export interface RunRevisionRow {
  key: string;
  item_type: string;
  name: string;
  revision: number;
  ref: string;
  node_id: string;
  status: string;
  change_reason: string | null;
  created_at: string | null;
  is_current: boolean;
  baselined_by_gate: string | null;
}

export interface RunChanges {
  run_id: string;
  revisions: RunRevisionRow[];
  baselines: BaselineSummary[];
}

export interface RevisionIndexEntry {
  key: string;
  item_type: string;
  revision: number;
  ref: string;
  status: string;
  current: boolean;
  revision_count: number;
  via: 'revision' | 'constraint_set';
}

export async function getCurrentView(projectId: string): Promise<CurrentView> {
  const { data } = await apiClient.get<CurrentView>('/twin/current-view', {
    params: { project_id: projectId },
  });
  return data;
}

export async function getItemHistory(key: string, projectId?: string): Promise<ItemHistory> {
  const { data } = await apiClient.get<ItemHistory>(`/twin/items/${encodeURIComponent(key)}/revisions`, {
    params: projectId ? { project_id: projectId } : undefined,
  });
  return data;
}

export async function getItemDiff(
  key: string,
  a?: number,
  b?: number,
  projectId?: string,
): Promise<ItemDiff> {
  const params: Record<string, string> = {};
  if (a) params.a = String(a);
  if (b) params.b = String(b);
  if (projectId) params.project_id = projectId;
  const { data } = await apiClient.get<ItemDiff>(`/twin/items/${encodeURIComponent(key)}/diff`, { params });
  return data;
}

export async function listBaselines(projectId: string): Promise<BaselineSummary[]> {
  const { data } = await apiClient.get<{ baselines: BaselineSummary[] }>('/twin/baselines', {
    params: { project_id: projectId },
  });
  return data.baselines;
}

export async function getBaselineDiff(a: string, b: string): Promise<BaselineDiff> {
  const { data } = await apiClient.get<BaselineDiff>('/twin/baselines/diff', { params: { a, b } });
  return data;
}

export async function getRunChanges(runId: string, projectId?: string): Promise<RunChanges> {
  const { data } = await apiClient.get<RunChanges>(`/twin/runs/${encodeURIComponent(runId)}/changes`, {
    params: projectId ? { project_id: projectId } : undefined,
  });
  return data;
}

/** Read-only badge data: degrades to an empty map so a page never breaks on it. */
export async function getRevisionIndex(projectId: string): Promise<Record<string, RevisionIndexEntry>> {
  try {
    const { data } = await apiClient.get<{ nodes: Record<string, RevisionIndexEntry> }>(
      '/twin/revision-index',
      { params: { project_id: projectId } },
    );
    return data.nodes;
  } catch {
    return {};
  }
}
