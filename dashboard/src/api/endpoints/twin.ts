import type { TwinNode, TwinRelationship } from '../../types/twin';

const MOCK_NODES: TwinNode[] = [
  { id: 'node-001', name: 'bracket-v1.step', type: 'artifact', domain: 'mechanical', status: 'valid', properties: { format: 'STEP', fileSize: 245000, material: 'Aluminum 6061' }, updatedAt: new Date(Date.now() - 2 * 3600_000).toISOString() },
  { id: 'node-002', name: 'main-schematic.kicad_sch', type: 'artifact', domain: 'electronics', status: 'valid', properties: { format: 'KiCad', sheets: 3 }, updatedAt: new Date(Date.now() - 4 * 3600_000).toISOString() },
  { id: 'node-003', name: 'pcb-layout.kicad_pcb', type: 'artifact', domain: 'electronics', status: 'warning', properties: { format: 'KiCad', layers: 4, drcErrors: 2 }, updatedAt: new Date(Date.now() - 1 * 3600_000).toISOString() },
  { id: 'node-004', name: 'max-stress < 250MPa', type: 'constraint', domain: 'mechanical', status: 'valid', properties: { limit: 250, unit: 'MPa', actual: 180 }, updatedAt: new Date(Date.now() - 6 * 3600_000).toISOString() },
  { id: 'node-005', name: 'min-clearance > 0.15mm', type: 'constraint', domain: 'electronics', status: 'error', properties: { limit: 0.15, unit: 'mm', actual: 0.12 }, updatedAt: new Date(Date.now() - 1 * 3600_000).toISOString() },
  { id: 'node-006', name: 'firmware-main.c', type: 'artifact', domain: 'firmware', status: 'valid', properties: { format: 'C', lines: 1240 }, updatedAt: new Date(Date.now() - 12 * 3600_000).toISOString() },
  { id: 'node-007', name: 'enclosure-v2.step', type: 'artifact', domain: 'mechanical', status: 'valid', properties: { format: 'STEP', fileSize: 580000, material: 'ABS' }, updatedAt: new Date(Date.now() - 24 * 3600_000).toISOString() },
  { id: 'node-008', name: 'v1.0.0', type: 'version', domain: 'system', status: 'active', properties: { tag: 'v1.0.0', branch: 'main' }, updatedAt: new Date(Date.now() - 48 * 3600_000).toISOString() },
  { id: 'node-009', name: 'power-budget < 500mW', type: 'constraint', domain: 'electronics', status: 'valid', properties: { limit: 500, unit: 'mW', actual: 320 }, updatedAt: new Date(Date.now() - 8 * 3600_000).toISOString() },
  { id: 'node-010', name: 'BOM export', type: 'artifact', domain: 'manufacturing', status: 'valid', properties: { format: 'CSV', components: 42 }, updatedAt: new Date(Date.now() - 3 * 3600_000).toISOString() },
];

const MOCK_RELATIONSHIPS: TwinRelationship[] = [
  { id: 'rel-001', sourceId: 'node-001', targetId: 'node-004', type: 'constrained_by', label: 'Stress constraint' },
  { id: 'rel-002', sourceId: 'node-002', targetId: 'node-003', type: 'generates', label: 'PCB from schematic' },
  { id: 'rel-003', sourceId: 'node-003', targetId: 'node-005', type: 'constrained_by', label: 'Clearance constraint' },
  { id: 'rel-004', sourceId: 'node-002', targetId: 'node-009', type: 'constrained_by', label: 'Power budget' },
];

export async function getTwinNodes(): Promise<TwinNode[]> {
  return MOCK_NODES;
}

export async function getTwinNode(id: string): Promise<TwinNode | undefined> {
  return MOCK_NODES.find((n) => n.id === id);
}

export async function getTwinRelationships(): Promise<TwinRelationship[]> {
  return MOCK_RELATIONSHIPS;
}
