import apiClient from '../client';

/**
 * Interface contracts (lamp golden path, api_gateway/twin/interface_contract_routes.py).
 *
 * A typed, versioned agreement between domains (CAD to EDA, EDA to firmware,
 * CAD and EDA to simulation, CAD and EDA to manufacturing): coupled
 * parameters with unit, tolerance, owner and basis, the items implementing
 * each side, the agreements per owning domain and the latest recorded check.
 */

const enc = encodeURIComponent;

export type ContractStatus = 'proposed' | 'agreed' | 'violated' | 'stale';
export type CheckOutcome = 'satisfied' | 'violated' | 'not_evaluated' | 'stale';
export type ValueBasis = 'stated' | 'estimated' | 'assumed' | 'unknown';

export interface CoupledParameter {
  name: string;
  label: string;
  value: unknown;
  unit: string;
  tolerance: number | null;
  owner: string;
  basis: ValueBasis;
  note: string;
}

export interface ContractLink {
  role: string;
  label: string;
  domain: string;
  item_types: string[];
  item_key: string | null;
  produced_by: string;
  linked: boolean;
  head_ref: string | null;
}

export interface ContractAgreement {
  domain: string;
  contract_revision: number;
  interface_version: number;
  decided_by: string;
  decision_id: string;
  at: string;
}

export interface ContractCheck {
  id: string;
  label: string;
  plain: string;
  outcome: CheckOutcome;
  message: string;
  values: Record<string, unknown>;
  missing: string[];
  inputs: string[];
  parameters: string[];
}

export interface LatestCheck {
  record_id: string;
  staleness: string;
  stale: boolean;
  staleness_reason: string | null;
  validation: { status: string; input_revisions: string[] };
  outcome: CheckOutcome;
  checks: ContractCheck[];
  inputs: string[];
  summary: string;
  evaluated_at: string;
}

export interface InterfaceContractView {
  key: string;
  ref: string;
  revision: number;
  interface_version: number;
  kind: 'cad_eda' | 'eda_firmware' | 'simulation' | 'manufacturing';
  title: string;
  plain: string;
  owning_domains: string[];
  parameters: CoupledParameter[];
  links: ContractLink[];
  artifacts: { kind: string; label: string; parts: string[]; produced_by: string; rule: string }[];
  rationale: string;
  agreements: ContractAgreement[];
  awaiting_agreement: string[];
  status: ContractStatus;
  create_line: string;
  latest_check: LatestCheck | null;
}

export interface ContractsResponse {
  project_id: string;
  contracts: InterfaceContractView[];
}

export const contractKeys = {
  all: ['interface-contracts'] as const,
  project: (projectId: string) => ['interface-contracts', projectId] as const,
};

/** `GET /v1/twin/projects/{id}/contracts`. */
export async function getContracts(projectId: string, signal?: AbortSignal): Promise<ContractsResponse> {
  const { data } = await apiClient.get<ContractsResponse>(`/twin/projects/${enc(projectId)}/contracts`, { signal });
  return data;
}

/** `POST /v1/twin/projects/{id}/contracts/check`: evaluate and record every contract (or one). */
export async function checkContracts(projectId: string, key?: string): Promise<{ checks: unknown[] }> {
  const { data } = await apiClient.post<{ checks: unknown[] }>(
    `/twin/projects/${enc(projectId)}/contracts/check`,
    key ? { key } : {},
  );
  return data;
}

/** `POST /v1/twin/projects/{id}/contracts/{key}/agree`: one owning domain agrees. */
export async function agreeContract(
  projectId: string,
  key: string,
  domain: string,
): Promise<{ decision_id: string | null; created: boolean; contract: string }> {
  const { data } = await apiClient.post<{ decision_id: string | null; created: boolean; contract: string }>(
    `/twin/projects/${enc(projectId)}/contracts/${enc(key)}/agree`,
    { domain },
  );
  return data;
}

/** A parameter's value as text, with its unit and tolerance; "unknown" when nobody knows it. */
export function parameterText(p: CoupledParameter): string {
  if (p.basis === 'unknown' || p.value === null || p.value === undefined) return 'unknown';
  const unit = p.unit ? ` ${p.unit}` : '';
  const tol = p.tolerance !== null && p.tolerance !== undefined ? ` ± ${p.tolerance}${unit}` : '';
  const v = p.value;
  let text: string;
  if (typeof v === 'number' || typeof v === 'string') text = `${v}${unit}`;
  else if (Array.isArray(v)) {
    text = v
      .map((x) => (x && typeof x === 'object' ? Object.values(x as Record<string, unknown>).join(', ') : String(x)))
      .join('; ');
    text = `${text}${unit}`;
  } else if (v && typeof v === 'object') {
    text = `${Object.entries(v as Record<string, unknown>)
      .map(([k, x]) => `${k} ${String(x)}`)
      .join(', ')}${unit}`;
  } else text = String(v);
  return `${text}${tol}`;
}

/** The plain words Create uses for where a contract stands. */
export function plainState(c: InterfaceContractView): string {
  if (!c.latest_check) return 'not checked yet';
  if (c.latest_check.stale || c.status === 'stale') return 'needs checking again after a change';
  switch (c.latest_check.outcome) {
    case 'satisfied':
      return 'checked, it fits';
    case 'violated':
      return 'does not fit yet';
    case 'stale':
      return 'needs checking again after a change';
    default:
      return 'not checked yet';
  }
}
