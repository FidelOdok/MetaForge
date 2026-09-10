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

// ---------------------------------------------------------------------------
// Assembly export (MET-721)
// ---------------------------------------------------------------------------

export type JointType = 'fixed' | 'slider' | 'revolute' | 'cylindrical' | 'ball';

export interface JointSpec {
  name: string;
  type: JointType;
  base: string;
  follower: string;
  axis: [number, number, number];
  anchor: [number, number, number];
  limits?: Record<string, number>;
}

export interface PartRef {
  node_id: string;
  link_name: string;
  material?: string;
  density_kg_m3?: number;
}

export interface UrdfAssemblyExportRequest {
  parts: PartRef[];
  joints: JointSpec[];
  robot_name?: string;
  mesh_format?: MeshFormat;
  mesh_uri_prefix?: string;
  xacro?: boolean;
}

export interface SdfAssemblyExportRequest {
  parts: PartRef[];
  joints: JointSpec[];
  model_name?: string;
  mesh_format?: MeshFormat;
  static?: boolean;
  world_name?: string;
}

export interface UsdAssemblyExportRequest {
  parts: PartRef[];
  joints: JointSpec[];
  robot_name?: string;
}

export interface Ros2LaunchRequest {
  robot_name: string;
  default_urdf_path: string;
  include_joint_state_publisher_gui?: boolean;
  include_rviz?: boolean;
}

interface AssemblyExportFields {
  output_file: ExportFile;
  mesh_files: ExportFile[];
  link_names: string[];
  joint_names: string[];
}

export interface UrdfAssemblyExportResponse extends AssemblyExportFields {
  robot_name: string;
}

export interface SdfAssemblyExportResponse extends AssemblyExportFields {
  model_name: string;
}

export interface UsdAssemblyExportResponse extends AssemblyExportFields {
  robot_name: string;
}

export interface Ros2LaunchResponse {
  output_file: ExportFile;
  robot_name: string;
  default_urdf_path: string;
}

export async function exportUrdfAssembly(
  req: UrdfAssemblyExportRequest,
): Promise<UrdfAssemblyExportResponse> {
  const { data } = await apiClient.post<UrdfAssemblyExportResponse>('/cad-export/urdf-assembly', req);
  return data;
}

export async function exportSdfAssembly(
  req: SdfAssemblyExportRequest,
): Promise<SdfAssemblyExportResponse> {
  const { data } = await apiClient.post<SdfAssemblyExportResponse>('/cad-export/sdf-assembly', req);
  return data;
}

export async function exportUsdAssembly(
  req: UsdAssemblyExportRequest,
): Promise<UsdAssemblyExportResponse> {
  const { data } = await apiClient.post<UsdAssemblyExportResponse>('/cad-export/usd-assembly', req);
  return data;
}

export async function generateRos2Launch(req: Ros2LaunchRequest): Promise<Ros2LaunchResponse> {
  const { data } = await apiClient.post<Ros2LaunchResponse>('/cad-export/ros2-launch', req);
  return data;
}

// ---------------------------------------------------------------------------
// Session introspection (MET-721) — reuse joints an agent already recorded
// via chat (freecad.add_assembly_joint) IF that FreeCAD session is still
// open (default 30 min idle TTL). There is no lookup by Twin/assembly node
// id — joints are never persisted anywhere durable, so this only works for
// a session_id the caller already knows (e.g. one echoed by a recent chat
// turn), never for an arbitrary historical assembly.
// ---------------------------------------------------------------------------

export interface SessionObject {
  obj_id: string;
  kind: string;
  name: string;
  order: number;
}

export interface SessionSummary {
  session_id: string;
  name: string;
  object_count: number;
  objects: SessionObject[];
}

export interface SessionJointsResponse {
  joints: JointSpec[];
}

export async function getSessionSummary(sessionId: string): Promise<SessionSummary> {
  const { data } = await apiClient.get<SessionSummary>(
    `/cad-export/sessions/${encodeURIComponent(sessionId)}`,
  );
  return data;
}

export async function getSessionJoints(sessionId: string): Promise<SessionJointsResponse> {
  const { data } = await apiClient.get<SessionJointsResponse>(
    `/cad-export/sessions/${encodeURIComponent(sessionId)}/joints`,
  );
  return data;
}

/** Prefix a gateway-relative download_url (e.g. "/v1/cad-export/download/...")
 * for use in a raw `<a href>` outside apiClient — same idiom TwinViewerPage
 * already applies to glb_url. */
export function toDownloadHref(url: string): string {
  return url.startsWith('/v1/') ? `/api${url}` : url;
}
