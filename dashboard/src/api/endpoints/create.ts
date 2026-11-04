import apiClient from '../client';

/**
 * The Create view's records. Mirrors `api_gateway/create/models.py`; the
 * project, conversation and work products are read from their own endpoints.
 */

const SURFACE_HEADERS = { 'X-MetaForge-Surface': 'dashboard' } as const;

export const BRIEF_KEYS = [
  'intended_use',
  'size_and_fit',
  'appearance',
  'materials',
  'design_budget',
  'manufacturing_budget',
  'quantity',
  'making_context',
  'success_criteria',
  'inspection_needs',
] as const;
export type BriefKey = (typeof BRIEF_KEYS)[number];

export type BriefStatus = 'confirmed' | 'assumed' | 'missing';
export type MeasurementStatus = 'user_confirmed' | 'estimated' | 'unconfirmed';
export type MakeRoute = 'home' | 'workshop' | 'external';
export type InspectionMethod = 'camera' | 'measurement' | 'telemetry' | 'operator' | 'instrument';
export type ResultStatus = 'passed' | 'failed' | 'inconclusive' | 'awaiting_review';
export type BuildStatus = 'preparing' | 'released' | 'making' | 'completed' | 'failed' | 'cancelled';
export type EvidenceKind = 'photo' | 'measurement' | 'telemetry' | 'note';
export type LengthUnit = 'mm' | 'cm' | 'm' | 'in' | 'ft';

export type Stage =
  | 'concept'
  | 'design_in_development'
  | 'checked_design'
  | 'ready_for_making'
  | 'released_for_making'
  | 'build_completed'
  | 'accepted_product';

export interface BriefItem {
  value: string;
  status: BriefStatus;
  source: 'user' | 'agent';
  updated_at?: string;
}

export interface Measurement {
  id: string;
  label: string;
  value: number;
  unit: LengthUnit;
  status: MeasurementStatus;
  critical: boolean;
  source: 'typed' | 'photo' | 'sketch' | 'model' | 'camera';
  reference_id?: string | null;
  scale_reference?: string | null;
  note?: string;
}

export interface Assumption {
  id: string;
  text: string;
  status: 'open' | 'confirmed' | 'revised';
}

/** What a reference is for (FORGE-794). Only the last two are authoritative. */
export const REFERENCE_ROLES = [
  'inspiration',
  'geometry',
  'functional_demo',
  'existing_design',
  'verification_evidence',
  'specification',
  'manufacturing_rule',
] as const;
export type ReferenceRole = (typeof REFERENCE_ROLES)[number];

export const REFERENCE_ROLE_LABELS: Record<ReferenceRole, string> = {
  inspiration: 'Inspiration',
  geometry: 'Shape',
  functional_demo: 'How it works',
  existing_design: 'Current design',
  verification_evidence: 'Evidence',
  specification: 'Specification',
  manufacturing_rule: 'Making rule',
};

export interface ReferenceAssociation {
  kind: 'project' | 'component' | 'parameter' | 'plane' | 'interface' | 'requirement';
  id: string;
  label?: string;
}

export interface FactCitation {
  reference_id: string;
  region: string;
  source_hash: string;
  excerpt: string;
}

export interface ReferenceFact {
  id: string;
  parameter: string;
  value: number | null;
  text: string;
  unit: string;
  /** `stated` only for an explicit value in a specification or making rule. Never measured. */
  basis: 'stated' | 'estimated';
  uncertainty: 'low' | 'medium' | 'high';
  citation: FactCitation;
}

export interface ReferenceRecord {
  work_product_id: string;
  name: string;
  role: ReferenceRole;
  associations: ReferenceAssociation[];
  active: boolean;
  protected_features: string[];
  source_hash: string;
  versions: Array<{ version: number; role: ReferenceRole; active: boolean; note: string; changed_at: string }>;
  facts: ReferenceFact[];
  disclosures: Array<{ provider: string; model: string; purpose: string; sent_at: string }>;
}

export interface ReferenceConflict {
  key: string;
  parameter: string;
  summary: string;
  citations: FactCitation[];
}

export interface Brief {
  items: Partial<Record<BriefKey, BriefItem>>;
  measurements: Measurement[];
  assumptions: Assumption[];
  references: string[];
  /** Role, inclusion, protected features, history and facts of the references. */
  reference_details?: ReferenceRecord[];
}

export interface RouteChoice {
  route: MakeRoute | null;
  process: string;
  machine_id: string | null;
  material: string;
  supplier: string;
  note: string;
}

export interface InspectionCheck {
  id: string;
  feature: string;
  condition: string;
  evidence: string;
  method: InspectionMethod;
  reviewer: 'person' | 'agent';
  blocking: boolean;
}

export interface Revision {
  work_product_id: string;
  version: number | null;
  label: string;
}

export interface Estimate {
  value: string;
  basis: string;
}

export interface Evidence {
  id: string;
  kind: EvidenceKind;
  check_id: string | null;
  work_product_id: string | null;
  upload_id: string | null;
  value: number | null;
  unit: string | null;
  note: string;
  recorded_by: string;
  surface: string;
  created_at: string;
}

export interface CheckResult {
  status: ResultStatus;
  note: string;
  confidence: number | null;
  evidence_ids: string[];
  decided_by: string;
  surface: string;
  updated_at: string;
}

export interface BuildDecision {
  decision: 'accept' | 'reject';
  reason: string;
  limitations: string[];
  approver: string;
  approver_verified: boolean;
  surface: string;
  decided_at: string;
}

export interface Build {
  id: string;
  number: number;
  revision: Revision;
  route: MakeRoute;
  process: string;
  machine_id: string | null;
  material: string;
  settings: Record<string, string>;
  estimates: Record<string, Estimate>;
  status: BuildStatus;
  status_note: string;
  checks: InspectionCheck[];
  release: { approver: string; approver_verified: boolean; surface: string; released_at: string } | null;
  evidence: Evidence[];
  results: Record<string, CheckResult>;
  acceptance: BuildDecision | null;
  created_at: string;
  updated_at: string;
}

export interface HistoryEntry {
  at: string;
  actor: string;
  surface: string;
  change: string;
  summary: string;
}

export interface Creation {
  project_id: string;
  version: number;
  brief: Brief;
  route: RouteChoice;
  inspection_plan: InspectionCheck[];
  builds: Build[];
  history: HistoryEntry[];
  updated_at: string;
}

export interface Blocker {
  id: string;
  message: string;
  action: string;
}

export interface Readiness {
  stage: Stage;
  stage_index: number;
  blockers: Blocker[];
  next_action: string;
  has_concept: boolean;
  has_engineering_model: boolean;
}

export interface CreationView {
  creation: Creation;
  readiness: Readiness;
  brief_keys: string[];
}

/** A write made against an older version; `current` is what is stored now. */
export interface CreationConflict {
  message: string;
  current: Creation;
}

interface Versioned {
  expected_version?: number | null;
  summary?: string;
}

const base = (projectId: string) => `/create/${encodeURIComponent(projectId)}`;
const build = (projectId: string, buildId: string) =>
  `${base(projectId)}/builds/${encodeURIComponent(buildId)}`;

export async function getCreation(projectId: string): Promise<CreationView> {
  const { data } = await apiClient.get<CreationView>(base(projectId));
  return data;
}

export async function putBrief(projectId: string, brief: Brief, v: Versioned = {}): Promise<CreationView> {
  const { data } = await apiClient.put<CreationView>(`${base(projectId)}/brief`, { brief, ...v });
  return data;
}

export async function putRoute(projectId: string, route: RouteChoice, v: Versioned = {}): Promise<CreationView> {
  const { data } = await apiClient.put<CreationView>(`${base(projectId)}/route`, { route, ...v });
  return data;
}

export async function putInspectionPlan(
  projectId: string,
  checks: InspectionCheck[],
  v: Versioned = {},
): Promise<CreationView> {
  const { data } = await apiClient.put<CreationView>(`${base(projectId)}/inspection-plan`, { checks, ...v });
  return data;
}

export interface BuildCreatePayload extends Versioned {
  revision: Revision;
  process?: string;
  machine_id?: string | null;
  material?: string;
  settings?: Record<string, string>;
  estimates?: Record<string, Estimate>;
}

export async function prepareBuild(projectId: string, payload: BuildCreatePayload): Promise<CreationView> {
  const { data } = await apiClient.post<CreationView>(`${base(projectId)}/builds`, payload);
  return data;
}

export async function releaseBuild(projectId: string, buildId: string, v: Versioned = {}): Promise<CreationView> {
  const { data } = await apiClient.post<CreationView>(`${build(projectId, buildId)}/release`, v, {
    headers: SURFACE_HEADERS,
  });
  return data;
}

export async function setBuildStatus(
  projectId: string,
  buildId: string,
  status: BuildStatus,
  note = '',
  v: Versioned = {},
): Promise<CreationView> {
  const { data } = await apiClient.post<CreationView>(
    `${build(projectId, buildId)}/status`,
    { status, note, ...v },
    { headers: SURFACE_HEADERS },
  );
  return data;
}

export interface EvidencePayload extends Versioned {
  kind: EvidenceKind;
  check_id?: string | null;
  work_product_id?: string | null;
  upload_id?: string | null;
  value?: number | null;
  unit?: string | null;
  note?: string;
}

export async function addEvidence(
  projectId: string,
  buildId: string,
  payload: EvidencePayload,
): Promise<CreationView> {
  const { data } = await apiClient.post<CreationView>(`${build(projectId, buildId)}/evidence`, payload, {
    headers: SURFACE_HEADERS,
  });
  return data;
}

export async function setCheckResult(
  projectId: string,
  buildId: string,
  checkId: string,
  payload: { status: ResultStatus; note?: string } & Versioned,
): Promise<CreationView> {
  const { data } = await apiClient.put<CreationView>(
    `${build(projectId, buildId)}/results/${encodeURIComponent(checkId)}`,
    payload,
    { headers: SURFACE_HEADERS },
  );
  return data;
}

export async function decideBuild(
  projectId: string,
  buildId: string,
  payload: { decision: 'accept' | 'reject'; reason?: string; limitations?: string[] } & Versioned,
): Promise<CreationView> {
  const { data } = await apiClient.post<CreationView>(`${build(projectId, buildId)}/accept`, payload, {
    headers: SURFACE_HEADERS,
  });
  return data;
}

export async function duplicateCreation(
  projectId: string,
  name: string,
): Promise<{ project_id: string; name: string }> {
  const { data } = await apiClient.post<{ project_id: string; name: string }>(`${base(projectId)}/duplicate`, {
    name,
  });
  return data;
}

export async function deleteCreation(projectId: string): Promise<void> {
  await apiClient.delete(base(projectId));
}

export interface ReferencesView extends CreationView {
  reference: ReferenceRecord | null;
  conflicts: Array<Record<string, unknown>>;
}

export interface ReferenceUpdate {
  role?: ReferenceRole;
  active?: boolean;
  protected_features?: string[];
  associations?: ReferenceAssociation[];
}

/** `PUT /v1/create/{id}/references/{wp}`: role, inclusion, protected features. */
export async function putReference(projectId: string, workProductId: string, update: ReferenceUpdate): Promise<ReferencesView> {
  const { data } = await apiClient.put<ReferencesView>(
    `${base(projectId)}/references/${encodeURIComponent(workProductId)}`,
    update,
    { headers: SURFACE_HEADERS },
  );
  return data;
}

/** `POST /v1/create/{id}/references/{wp}/facts`: candidate facts with citations. */
export async function extractReferenceFacts(projectId: string, workProductId: string, note = ''): Promise<ReferencesView> {
  const { data } = await apiClient.post<ReferencesView>(
    `${base(projectId)}/references/${encodeURIComponent(workProductId)}/facts`,
    { note },
    { headers: SURFACE_HEADERS },
  );
  return data;
}

/** The record for a reference, or the default an unconfigured one has. */
export function referenceRecord(brief: Brief | undefined, workProductId: string, name = ''): ReferenceRecord {
  const found = brief?.reference_details?.find((r) => r.work_product_id === workProductId);
  return (
    found ?? {
      work_product_id: workProductId,
      name,
      role: 'inspiration',
      associations: [],
      active: true,
      protected_features: [],
      source_hash: '',
      versions: [],
      facts: [],
      disclosures: [],
    }
  );
}

/** The 409 body of a stale write, when that is what an error is. */
export function conflictOf(error: unknown): CreationConflict | null {
  const detail = (error as { response?: { status?: number; data?: { detail?: unknown } } })?.response;
  if (detail?.status !== 409) return null;
  const body = detail.data?.detail as CreationConflict | undefined;
  return body && typeof body === 'object' && 'current' in body ? body : null;
}

/** The gateway's own words for a refused request, for showing to a person. */
export function refusalOf(error: unknown): string {
  const response = (error as { response?: { data?: { detail?: unknown } } })?.response;
  const detail = response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && 'message' in detail) return String((detail as { message: unknown }).message);
  return (error as Error)?.message ?? 'Something went wrong';
}

/** A registered machine (`/v1/library/machines`, layer `instance`). */
export interface MachineInstance {
  id: string;
  name: string;
  kind: string;
  processes: string[];
  route: string;
  location: string;
  /** What the library was last told. Not a live reading of the machine. */
  status: string;
  connection: Record<string, unknown>;
}

export async function listMachineInstances(): Promise<MachineInstance[]> {
  const { data } = await apiClient.get<{ machines: MachineInstance[] }>('/library/machines', {
    params: { layer: 'instance' },
  });
  return data.machines ?? [];
}
