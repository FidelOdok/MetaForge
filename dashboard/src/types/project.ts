/** Project and work product types for the dashboard. */

export interface ProjectWorkProduct {
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
  work_products: ProjectWorkProduct[];
  agentCount: number;
  lastUpdated: string;
  createdAt: string;
}
