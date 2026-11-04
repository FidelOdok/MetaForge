/**
 * What kind of thing a twin node is, for the graph's "Show" filter.
 *
 * A real project's twin is mostly records: on the sandbox's Chair project, 29
 * of 59 nodes are design decisions, which turned the graph into a wall of
 * cards and crossing lines. The graph now shows designs, requirements and
 * analysis by default and keeps decisions one click away once a twin is
 * large; every hidden node is counted, never dropped silently.
 */

import type { TwinNode } from '../types/twin';

export type NodeKind = 'designs' | 'requirements' | 'decisions' | 'analysis' | 'other';

export const NODE_KINDS: readonly { id: NodeKind; label: string }[] = [
  { id: 'designs', label: 'Designs' },
  { id: 'requirements', label: 'Requirements' },
  { id: 'decisions', label: 'Decisions' },
  { id: 'analysis', label: 'Analysis' },
  { id: 'other', label: 'Other' },
];

/** Above this many nodes, decisions start hidden. */
export const LARGE_TWIN = 30;

const DESIGNS = new Set([
  'cad_model',
  'assembly',
  'design_sketch',
  'technical_drawing',
  'render',
  'image',
  'mesh',
  'pcb_layout',
  'schematic',
  'firmware_source',
  'robot_description',
  'gcode',
]);
const REQUIREMENTS = new Set([
  'constraint_set',
  'requirement',
  'requirements',
  'prd',
  'intent',
  'stakeholder_need',
  'objective',
  'design_value',
  'budget',
]);
const DECISIONS = new Set(['design_decision', 'decision', 'adr']);
const ANALYSIS = new Set([
  'load_case',
  'simulation_result',
  'fea_result',
  'evidence',
  'test_plan',
  'test_result',
  'verification_case',
  'analysis',
]);

export function kindOf(node: Pick<TwinNode, 'type' | 'properties'>): NodeKind {
  const t = String(node.properties?.wp_type ?? node.type ?? '');
  if (DESIGNS.has(t)) return 'designs';
  if (REQUIREMENTS.has(t)) return 'requirements';
  if (DECISIONS.has(t)) return 'decisions';
  if (ANALYSIS.has(t)) return 'analysis';
  return 'other';
}

/** Everything for a small twin; a large one starts without the decision records. */
export function defaultKinds(total: number): ReadonlySet<NodeKind> {
  const all = new Set<NodeKind>(NODE_KINDS.map((k) => k.id));
  if (total > LARGE_TWIN) all.delete('decisions');
  return all;
}

export function countKinds(nodes: Pick<TwinNode, 'type' | 'properties'>[]): Record<NodeKind, number> {
  const counts: Record<NodeKind, number> = { designs: 0, requirements: 0, decisions: 0, analysis: 0, other: 0 };
  for (const n of nodes) counts[kindOf(n)] += 1;
  return counts;
}
