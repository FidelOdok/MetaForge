import type { TwinNode, TwinRelationship, WorkProductRevision } from '../../types/twin';
import apiClient from '../client';

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

interface TwinRelationshipListApiResponse {
  relationships: TwinRelationship[];
  total: number;
}

export async function getTwinRelationships(): Promise<TwinRelationship[]> {
  try {
    const response = await apiClient.get<TwinRelationshipListApiResponse>('/twin/relationships');
    return response.data.relationships;
  } catch {
    return [];
  }
}

export interface NodeVersionHistory {
  work_product_id: string;
  revisions: WorkProductRevision[];
  total: number;
}

export async function getNodeVersionHistory(nodeId: string): Promise<NodeVersionHistory> {
  const { data } = await apiClient.get<NodeVersionHistory>(`/twin/nodes/${nodeId}/versions`);
  return data;
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
