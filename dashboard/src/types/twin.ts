export type TwinNodeType = 'work_product' | 'constraint' | 'relationship' | 'version';

export interface TwinNode {
  id: string;
  name: string;
  type: TwinNodeType;
  domain: string;
  status: string;
  properties: Record<string, string | number | boolean>;
  updatedAt: string;
}

export interface TwinRelationship {
  id: string;
  sourceId: string;
  targetId: string;
  type: string;
  label: string;
}

export interface ImportWorkProductResponse {
  id: string;
  name: string;
  domain: string;
  wp_type: string;
  file_path: string;
  content_hash: string;
  format: string;
  metadata: Record<string, unknown>;
  project_id: string | null;
  created_at: string;
}

export type FileLinkStatus = 'synced' | 'changed' | 'disconnected';
export type FileLinkTool = 'kicad' | 'freecad' | 'cadquery' | 'none';

export interface FileLink {
  id: string;
  node_id: string;
  file_path: string;
  tool: FileLinkTool;
  watch: boolean;
  status: FileLinkStatus;
  last_synced_at: string | null;
  created_at: string;
}

export interface SyncResult {
  link_id: string;
  node_id: string;
  status: FileLinkStatus;
  changes: Record<string, { before: unknown; after: unknown }>;
  synced_at: string;
}
