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
  work_product_id: string;
  source_path: string;
  tool: FileLinkTool;
  watch: boolean;
  sync_status: FileLinkStatus;
  source_hash: string;
  last_synced_at: string;
  created_at: string;
}

export interface SyncResult {
  work_product_id: string;
  sync_status: FileLinkStatus;
  changes: Record<string, { before: unknown; after: unknown }>;
  synced_at: string;
}
