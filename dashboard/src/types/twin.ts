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

export interface WorkProductRevision {
  revision: number;
  created_at: string;
  content_hash: string;
  change_description: string;
  metadata_snapshot: Record<string, unknown>;
}
