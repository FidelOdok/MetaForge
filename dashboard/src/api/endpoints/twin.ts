import type { TwinNode, TwinRelationship, ImportWorkProductResponse, FileLink, FileLinkTool, SyncResult } from '../../types/twin';
import apiClient from '../client';

const MOCK_RELATIONSHIPS: TwinRelationship[] = [
  { id: 'rel-001', sourceId: 'node-001', targetId: 'node-004', type: 'constrained_by', label: 'Stress constraint' },
  { id: 'rel-002', sourceId: 'node-002', targetId: 'node-003', type: 'generates', label: 'PCB from schematic' },
  { id: 'rel-003', sourceId: 'node-003', targetId: 'node-005', type: 'constrained_by', label: 'Clearance constraint' },
  { id: 'rel-004', sourceId: 'node-002', targetId: 'node-009', type: 'constrained_by', label: 'Power budget' },
];

interface TwinNodeApiResponse {
  id: string;
  name: string;
  type: string;
  domain: string;
  status: string;
  properties: Record<string, string | number | boolean>;
  updatedAt: string;
}

interface TwinNodeListApiResponse {
  nodes: TwinNodeApiResponse[];
  total: number;
}

export async function getTwinNodes(projectId?: string): Promise<TwinNode[]> {
  // MET-491: scope to a project when one is selected; omit for all projects.
  const params = projectId ? { project_id: projectId } : undefined;
  const response = await apiClient.get<TwinNodeListApiResponse>('/twin/nodes', { params });
  return response.data.nodes.map((node): TwinNode => ({
    id: node.id,
    name: node.name,
    type: node.type as TwinNode['type'],
    domain: node.domain,
    status: node.status,
    properties: node.properties,
    updatedAt: node.updatedAt,
  }));
}

export async function getTwinNode(id: string): Promise<TwinNode | undefined> {
  try {
    const response = await apiClient.get<TwinNodeApiResponse>(`/twin/nodes/${id}`);
    const node = response.data;
    return {
      id: node.id,
      name: node.name,
      type: node.type as TwinNode['type'],
      domain: node.domain,
      status: node.status,
      properties: node.properties,
      updatedAt: node.updatedAt,
    };
  } catch {
    return undefined;
  }
}

export async function getTwinRelationships(): Promise<TwinRelationship[]> {
  // Live edges from the twin graph (backend already returns camelCase fields
  // matching TwinRelationship). Falls back to mocks if the endpoint is absent.
  try {
    const response = await apiClient.get<{ relationships: TwinRelationship[] }>(
      '/twin/relationships',
    );
    return response.data.relationships ?? [];
  } catch {
    return MOCK_RELATIONSHIPS;
  }
}

export interface NodeModelResult {
  hash: string;
  glb_url: string;
  metadata: {
    parts: { name: string; meshName: string; children: unknown[]; boundingBox?: Record<string, number> }[];
    materials: { name: string; color?: string }[];
    stats: { triangleCount: number; fileSize: number };
  };
  cached: boolean;
}

export async function getNodeModel(nodeId: string, quality = 'standard'): Promise<NodeModelResult> {
  const { data } = await apiClient.get<NodeModelResult>(`/twin/nodes/${nodeId}/model?quality=${quality}`);
  return data;
}

export async function importWorkProduct(
  formData: FormData,
  onUploadProgress?: (pct: number) => void,
): Promise<ImportWorkProductResponse> {
  const { data } = await apiClient.post<ImportWorkProductResponse>('/twin/import', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: onUploadProgress
      ? (evt) => {
          const pct = evt.total ? Math.round((evt.loaded * 100) / evt.total) : 0;
          onUploadProgress(pct);
        }
      : undefined,
  });
  return data;
}

export async function createLink(
  nodeId: string,
  payload: { file_path: string; tool: FileLinkTool; watch: boolean },
): Promise<FileLink> {
  const { data } = await apiClient.post<FileLink>(`/twin/nodes/${nodeId}/link`, payload);
  return data;
}

export async function getNodeLink(nodeId: string): Promise<FileLink | null> {
  try {
    const { data } = await apiClient.get<FileLink>(`/twin/nodes/${nodeId}/link`);
    return data;
  } catch (err: unknown) {
    if (
      err &&
      typeof err === 'object' &&
      'response' in err &&
      (err as { response?: { status?: number } }).response?.status === 404
    ) {
      return null;
    }
    throw err;
  }
}

export async function getAllLinks(projectId?: string): Promise<FileLink[]> {
  const params = projectId ? { project_id: projectId } : {};
  const { data } = await apiClient.get<FileLink[]>('/twin/links', { params });
  return data;
}

export async function deleteLink(nodeId: string): Promise<void> {
  await apiClient.delete(`/twin/nodes/${nodeId}/link`);
}

export async function syncNode(nodeId: string): Promise<SyncResult> {
  const { data } = await apiClient.post<SyncResult>(`/twin/nodes/${nodeId}/sync`);
  return data;
}

/** One snapshot in a work product's revision history (``WorkProductRevision``). */
export interface WorkProductRevision {
  revision: number;
  created_at: string;
  content_hash: string;
  change_description: string;
  metadata_snapshot: Record<string, unknown>;
}

interface WorkProductVersionHistoryRaw {
  work_product_id: string;
  revisions: WorkProductRevision[];
  total: number;
}

/**
 * Full revision history for a work product.
 *
 * Backed by ``GET /v1/twin/nodes/{node_id}/versions``, which returns a
 * ``WorkProductVersionHistory`` object (``{work_product_id, revisions,
 * total}``) — this unwraps `.revisions` so callers get a plain list.
 */
export async function getNodeVersionHistory(nodeId: string): Promise<WorkProductRevision[]> {
  const { data } = await apiClient.get<WorkProductVersionHistoryRaw>(`/twin/nodes/${nodeId}/versions`);
  return data.revisions ?? [];
}

// ── Work-product file download / open / preview (MET-483) ───────────────────
// The browser fetches blobs directly from the gateway via the Vite proxy, so
// these are full ``/api/v1`` URLs (not the apiClient baseURL-relative paths)
// suitable for <a href>, <img src>, and <iframe src>.
const FILE_API_BASE = '/api/v1';

/** URL the browser hits to open (inline) or download a work product's file. */
export function nodeFileUrl(nodeId: string, download = false): string {
  return `${FILE_API_BASE}/twin/nodes/${nodeId}/file${download ? '?download=true' : ''}`;
}

/** Fetch a text-previewable work-product blob as a string (for inline preview). */
export async function fetchNodeFileText(nodeId: string): Promise<string> {
  const { data } = await apiClient.get(`/twin/nodes/${nodeId}/file`, { responseType: 'text' });
  return typeof data === 'string' ? data : JSON.stringify(data, null, 2);
}

// ── Real boolean CSG cut between two committed CAD nodes (MET-612) ─────────

export type BooleanCutOperation = 'subtract' | 'union' | 'intersect';

export interface BooleanCutResult {
  node: TwinNode;
  operation: BooleanCutOperation;
  resultVolumeMm3: number;
  resultAreaMm2: number;
}

interface BooleanCutApiResponse {
  node: TwinNodeApiResponse;
  operation: string;
  result_volume_mm3: number;
  result_area_mm2: number;
}

/** POST /v1/twin/nodes/boolean-cut — subtract/union/intersect two STEP nodes,
 * committing a real new geometry node (not a client-side visual trick). */
export async function booleanCutNodes(
  targetNodeId: string,
  cutterNodeId: string,
  operation: BooleanCutOperation,
  resultName?: string,
): Promise<BooleanCutResult> {
  const { data } = await apiClient.post<BooleanCutApiResponse>('/twin/nodes/boolean-cut', {
    target_node_id: targetNodeId,
    cutter_node_id: cutterNodeId,
    operation,
    result_name: resultName,
  });
  return {
    node: {
      id: data.node.id,
      name: data.node.name,
      type: data.node.type as TwinNode['type'],
      domain: data.node.domain,
      status: data.node.status,
      properties: data.node.properties,
      updatedAt: data.node.updatedAt,
    },
    operation: data.operation as BooleanCutOperation,
    resultVolumeMm3: data.result_volume_mm3,
    resultAreaMm2: data.result_area_mm2,
  };
}
