export type TwinNodeType = 'work_product' | 'constraint' | 'relationship' | 'version';

export interface GeometryFeatures {
  parameters: Record<string, unknown>;
  properties: Record<string, unknown>;
}

// MET-740: a robot_description node's {parts, joints} — the exact shape
// the assembly-export request/response bodies use (see
// api/endpoints/cad-export.ts's PartRef/JointSpec). Kept loose here (not
// importing those types) to avoid a types/ -> api/ dependency; the shapes
// are duck-typed identically.
export interface AssemblyDescription {
  parts: { node_id: string; link_name: string; material?: string; density_kg_m3?: number }[];
  joints: {
    name: string;
    type: string;
    base: string;
    follower: string;
    axis: [number, number, number];
    anchor: [number, number, number];
    limits?: Record<string, number>;
  }[];
}

export interface TwinNode {
  id: string;
  name: string;
  type: TwinNodeType;
  domain: string;
  status: string;
  properties: Record<string, string | number | boolean>;
  updatedAt: string;
  // MET-630: structured geometry parameters/properties, kept separate from
  // `properties` above (which is scalar-only). Undefined for non-CAD nodes
  // or CAD nodes with no recorded geometry_features.
  geometryParameters?: GeometryFeatures;
  // MET-630: whether a git-versioned generation script backs this node
  // (fetchable via GET /twin/nodes/{id}/script).
  hasScript?: boolean;
  // MET-740: a robot_description node's {parts, joints} — undefined for
  // every other node type. Lets the Assembly export form reconstruct its
  // full state from an already-fetched node list, no extra round trip and
  // no live FreeCAD session required.
  assembly?: AssemblyDescription;
  // FORGE-250: a robot_description node's saved named poses
  // ({poseName: {jointName: value}}) — undefined for every other node
  // type, or a robot_description with no saved poses yet.
  poses?: Record<string, Record<string, number>>;
  // FORGE-305: a simulation_result node's mesh statistics ({num_nodes,
  // num_elements, ...}). Undefined for every other node type, or a result
  // recorded without them. Like geometryParameters above, this is here
  // because `properties` is scalar-only and silently drops objects.
  meshStats?: Record<string, unknown>;
  // FORGE-511: an assembly cad_model's part list (metadata.parts) — undefined
  // for every other node.
  assemblyParts?: AssemblyPart[];
  // FORGE-528: the project the node belongs to, when it has one.
  projectId?: string;
}

export interface AssemblyPartBox {
  min: [number, number, number];
  max: [number, number, number];
}

export interface AssemblyPart {
  node_id: string;
  name: string;
  material?: string | null;
  position_bbox_mm?: AssemblyPartBox | null;
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
