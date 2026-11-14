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
  engine: string;
  usage: string;
  limitations?: string[];
  status?: 'active' | 'deprecated' | 'withdrawn';
  artifacts: ModelArtifact[];
}

export interface LibraryComponent {
  id: string;
  name: string;
  title: string;
  family: string;
  origin: string;
  standard?: string;
  manufacturer?: string;
  mpn?: string;
  datasheet?: string;
  material?: string;
  specs: Record<string, SourcedValue>;
  suppliers?: Array<{ supplier: string; sku: string; url?: string; offer?: SupplierOffer }>;
  models?: ModelRepresentation[];
  form_code?: string;
  notes: string;
  version: number;
  updated_at: number;
}

export interface LibraryMaterial {
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
  connection?: { protocol?: string; url?: string };
  version: number;
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
