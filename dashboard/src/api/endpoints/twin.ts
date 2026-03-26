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

export async function getTwinNodes(): Promise<TwinNode[]> {
  const response = await apiClient.get<TwinNodeListApiResponse>('/twin/nodes');
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
  return MOCK_RELATIONSHIPS;
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

export async function getAllLinks(): Promise<FileLink[]> {
  const { data } = await apiClient.get<FileLink[]>('/twin/links');
  return data;
}

export async function deleteLink(nodeId: string): Promise<void> {
  await apiClient.delete(`/twin/nodes/${nodeId}/link`);
}

export async function syncNode(nodeId: string): Promise<SyncResult> {
  const { data } = await apiClient.post<SyncResult>(`/twin/nodes/${nodeId}/sync`);
  return data;
}
