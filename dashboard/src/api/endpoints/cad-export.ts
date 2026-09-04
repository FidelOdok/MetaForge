import apiClient from '../client';

// Robotics-sim export (MET-719/720). Field names mirror the backend's
// Pydantic schemas (api_gateway/cad_export/schemas.py) 1:1 rather than being
// remapped to camelCase — same convention as getNodeVersionHistory in
// twin.ts, since request AND response fields are already snake_case here.

export type MeshFormat = 'stl' | 'obj';

export interface ExportFile {
  filename: string;
  download_url: string;
}

export interface UrdfExportRequest {
  node_id: string;
  link_name?: string;
  material?: string;
  density_kg_m3?: number;
  mesh_format?: MeshFormat;
  mesh_uri_prefix?: string;
  xacro?: boolean;
}

export interface SdfExportRequest {
  node_id: string;
  model_name?: string;
  link_name?: string;
  material?: string;
  density_kg_m3?: number;
  mesh_format?: MeshFormat;
  static?: boolean;
  world_name?: string;
}

export interface UsdExportRequest {
  node_id: string;
  prim_name?: string;
  material?: string;
  density_kg_m3?: number;
}

interface SinglePartExportFields {
  output_file: ExportFile;
  mesh_file: ExportFile;
  mass_kg: number;
  center_of_mass_m: Record<string, number>;
  inertia_kgm2: Record<string, number>;
}

export interface UrdfExportResponse extends SinglePartExportFields {
  link_name: string;
  density_kg_m3: number;
}

export interface SdfExportResponse extends SinglePartExportFields {
  model_name: string;
  link_name: string;
  density_kg_m3: number;
}

export interface UsdExportResponse extends SinglePartExportFields {
  prim_name: string;
  triangle_count: number;
  density_kg_m3: number;
}

export async function exportUrdf(req: UrdfExportRequest): Promise<UrdfExportResponse> {
  const { data } = await apiClient.post<UrdfExportResponse>('/cad-export/urdf', req);
  return data;
}

export async function exportSdf(req: SdfExportRequest): Promise<SdfExportResponse> {
  const { data } = await apiClient.post<SdfExportResponse>('/cad-export/sdf', req);
  return data;
}

export async function exportUsd(req: UsdExportRequest): Promise<UsdExportResponse> {
  const { data } = await apiClient.post<UsdExportResponse>('/cad-export/usd', req);
  return data;
}

/** Prefix a gateway-relative download_url (e.g. "/v1/cad-export/download/...")
 * for use in a raw `<a href>` outside apiClient — same idiom TwinViewerPage
 * already applies to glb_url. */
export function toDownloadHref(url: string): string {
  return url.startsWith('/v1/') ? `/api${url}` : url;
}
