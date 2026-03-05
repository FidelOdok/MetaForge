/** Project and artifact types for the dashboard. */

export interface ProjectArtifact {
  id: string;
  name: string;
  type: 'schematic' | 'pcb' | 'cad_model' | 'firmware' | 'bom' | 'gerber';
  status: 'valid' | 'warning' | 'error' | 'unknown';
  updatedAt: string;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  status: 'active' | 'archived' | 'draft';
  artifacts: ProjectArtifact[];
  agentCount: number;
  lastUpdated: string;
  createdAt: string;
}
