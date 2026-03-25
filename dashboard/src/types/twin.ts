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
