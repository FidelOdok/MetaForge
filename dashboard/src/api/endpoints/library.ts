import apiClient from '../client';

/** A sourced value: a spec or a property, with where it came from. */
export interface SourcedValue {
  /** A number, a size as [x, y, z], or a list of names (a machine's materials). */
  value: number | string | Array<number | string>;
  unit: string;
  source_kind: string;
  source_ref: string;
  needs_review: boolean;
}

export interface SupplierOffer {
  read_on?: string;
  via?: string;
  currency?: string;
  breaks?: Array<{ qty: number; unit_price: number }>;
  stock?: number;
  lead_time_days?: number;
  confirmed?: boolean;
}

export interface ModelArtifact {
  path: string;
  url: string;
  sha256: string;
  size: number;
  licence: string;
  embedded?: string[];
  part?: string;
}

export interface ModelRepresentation {
  model_id: string;
  version: string;
  /** cad, physics, emulation, module or spice (FORGE-906). */
  kind?: string;
  engine: string;
  usage: string;
  limitations?: string[];
  status?: 'active' | 'deprecated' | 'withdrawn';
  artifacts: ModelArtifact[];
}

/** FORGE-906: one row from a datasheet's rating tables. */
export interface RatingRow {
  param: string;
  kind: 'abs_max' | 'recommended' | 'electrical';
  min: number | null;
  typ: number | null;
  max: number | null;
  unit: string;
  conditions: string;
  source_kind: string;
  source_ref: string;
  needs_review: boolean;
}

export type LifecycleStatus = 'active' | 'nrnd' | 'eol' | 'obsolete' | 'unknown';

export interface PartLifecycle {
  status: LifecycleStatus;
  source_kind: string;
  source_ref: string;
  read_on: string;
  last_time_buy?: string;
  needs_review: boolean;
}

export interface PartEcad {
  symbol?: string;
  footprint?: string;
  source_kind: string;
  source_ref: string;
  needs_review: boolean;
}

export type PartDataStatus = 'ok' | 'review' | 'missing' | 'bad';

/** FORGE-906: what a part has and lacks to be relied on in a design; derived by the gateway. */
export interface PartDataCheck {
  applies: boolean;
  complete: boolean | null;
  summary: string;
  items: Array<{ key: string; label: string; status: PartDataStatus; detail: string; required: boolean }>;
}

export interface LibraryComponent {
  /** FORGE-876: from the shared library (true) or the caller's own (false). */
  shared?: boolean;
  id: string;
  name: string;
  title: string;
  family: string;
  origin: string;
  standard?: string;
  manufacturer?: string;
  /** The organisation entry the maker is, when its name resolves (FORGE-450). */
  manufacturer_org?: string;
  mpn?: string;
  datasheet?: string;
  material?: string;
  specs: Record<string, SourcedValue>;
  suppliers?: Array<{ supplier: string; sku: string; url?: string; org?: string; offer?: SupplierOffer }>;
  models?: ModelRepresentation[];
  ratings?: RatingRow[];
  lifecycle?: PartLifecycle | Record<string, never>;
  ecad?: PartEcad | Record<string, never>;
  part_data?: PartDataCheck;
  form_code?: string;
  notes: string;
  version: number;
  updated_at: number;
}

export interface LibraryMaterial {
  /** FORGE-876: from the shared library (true) or the caller's own (false). */
  shared?: boolean;
  id: string;
  name: string;
  title: string;
  family: string;
  designation?: string;
  forms?: string[];
  properties: Record<string, SourcedValue>;
  notes: string;
  version: number;
}

export interface LibraryMachine {
  /** FORGE-876: from the shared library (true) or the caller's own (false). */
  shared?: boolean;
  id: string;
  layer: 'model' | 'instance';
  kind: string;
  make?: string;
  model?: string;
  name?: string;
  model_id?: string | null;
  processes?: string[];
  capabilities?: Record<string, SourcedValue>;
  route?: string;
  status?: string;
  location?: string;
  /** The organisation that runs it: a service bureau (FORGE-450). */
  operator_org?: string;
  connection?: { protocol?: string; url?: string };
  version: number;
}

export type OrgRole = 'manufacturer' | 'distributor' | 'fabricator' | 'test_lab';
export type OrgStatus = 'approved' | 'unreviewed' | 'blocked';
export const ORG_REGIONS = ['UK', 'EU', 'Europe', 'Asia', 'Americas', 'Oceania', 'Africa', 'Global'] as const;

/** A manufacturer, distributor, fabricator or test lab, recorded once however it is written. */
export interface LibraryOrganisation {
  /** FORGE-876: from the shared library (true) or the caller's own (false). */
  shared?: boolean;
  id: string;
  name: string;
  title: string;
  roles: OrgRole[];
  aliases: string[];
  status: OrgStatus;
  status_reason: string;
  country: string;
  regions: string[];
  website: string;
  quote_url: string;
  certifications: string[];
  contacts: Array<{ role: string; url?: string; email?: string }>;
  lifecycle_policy: string;
  connector: string;
  notes: string;
  version: number;
}

export type SearchKind =
  | 'component'
  | 'organisation'
  | 'material'
  | 'process'
  | 'machine'
  | 'appearance'
  | 'ergonomic_limit';

/** One library search result; ``matched`` names the fields that matched. */
export interface SearchHit {
  /** FORGE-876: from the shared library (true) or the caller's own (false). */
  shared?: boolean;
  kind: SearchKind;
  id: string;
  name: string;
  title: string;
  score: number;
  matched: string[];
  /** A component made or sold only by blocked organisations says why. */
  blocked?: string[];
  status?: OrgStatus;
  roles?: OrgRole[];
  manufacturer?: string;
  mpn?: string;
  layer?: string;
}

export interface SearchResult {
  query: string;
  total: number;
  results: SearchHit[];
}

export interface SearchParams {
  q: string;
  kinds?: SearchKind[];
  role?: OrgRole;
  region?: string;
  status?: OrgStatus;
  limit?: number;
}

/** One coloured group of a maker's model, as the gateway's mesh route returns it. */
export interface MeshPart {
  name: string;
  colour: [number, number, number] | null;
  colour_source: string;
  positions: number[];
  indices: number[];
}

export interface ComponentMesh {
  parts: MeshPart[];
  triangles: number;
  source: { licence?: string; model_id?: string; model_version?: string };
}

export interface ImportReport {
  added: Record<string, number>;
  skipped: Record<string, number>;
  failed: Array<{ kind: string; name: string; reason: string }>;
}

export async function listComponents(): Promise<LibraryComponent[]> {
  const { data } = await apiClient.get<{ entries: LibraryComponent[] }>('/library/components');
  return data.entries;
}

export async function listMaterials(): Promise<LibraryMaterial[]> {
  const { data } = await apiClient.get<{ entries: LibraryMaterial[] }>('/library/materials');
  return data.entries;
}

export async function listMachines(): Promise<LibraryMachine[]> {
  const { data } = await apiClient.get<{ machines: LibraryMachine[] }>('/library/machines');
  return data.machines;
}

export async function listOrganisations(): Promise<LibraryOrganisation[]> {
  const { data } = await apiClient.get<{ entries: LibraryOrganisation[] }>('/library/organisations');
  return data.entries;
}

/** The whole library searched at once, best first (FORGE-895). */
export async function searchLibrary(params: SearchParams): Promise<SearchResult> {
  const query = new URLSearchParams({ q: params.q });
  (params.kinds ?? []).forEach((k) => query.append('kind', k));
  if (params.role) query.set('role', params.role);
  if (params.region) query.set('region', params.region);
  if (params.status) query.set('status', params.status);
  query.set('limit', String(params.limit ?? 60));
  const { data } = await apiClient.get<SearchResult>(`/library/search?${query.toString()}`);
  return data;
}

export async function getComponentMesh(name: string): Promise<ComponentMesh> {
  // Converting a maker's STEP can take a while the first time; the gateway caches it.
  const { data } = await apiClient.get<ComponentMesh>(
    `/library/components/${encodeURIComponent(name)}/mesh`,
    { timeout: 300_000 },
  );
  return data;
}

export async function exportLibrary(history = true): Promise<Blob> {
  const { data } = await apiClient.get<unknown>('/library/export', {
    params: { history },
    timeout: 300_000,
  });
  return new Blob([JSON.stringify(data)], { type: 'application/json' });
}

export async function importLibrary(document: unknown): Promise<ImportReport> {
  const { data } = await apiClient.post<ImportReport>('/library/import', document, {
    timeout: 300_000,
  });
  return data;
}
