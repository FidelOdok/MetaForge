import type { ImpactResult, InterfaceListResponse } from '../../../types/impact';
import type { SceneGraph, SceneGraphLink, SceneGraphNode } from './types';

/** Domains left to right, the way a design usually flows. */
const DOMAIN_ORDER = ['sketch', 'mechanical', 'electronics', 'firmware', 'simulation', 'manufacturing', 'general'];
/** At most this many nodes are drawn; the rest are in the text views. */
export const MAX_SCENE_NODES = 12;

/**
 * The scene graph from the project's interfaces and, when the activity is an
 * impact, that impact's own graph (FORGE-793, section 11C: edges and domain
 * states come from the design dependency graph). `null` when there is no
 * graph, so the scene stays illustrative.
 */
export function sceneGraphFrom(
  interfaces: InterfaceListResponse | null | undefined,
  impact: ImpactResult | null | undefined,
): SceneGraph | null {
  const nodes = new Map<string, SceneGraphNode>();
  const links = new Map<string, SceneGraphLink>();
  for (const item of interfaces?.nodes ?? []) {
    nodes.set(item.ref, { id: item.ref, label: item.label || item.ref, domain: item.domain, role: 'idle' });
  }
  for (const edge of interfaces?.edges ?? []) {
    if (edge.status === 'removed') continue;
    links.set(`${edge.source.ref}>${edge.target.ref}`, {
      source: edge.source.ref,
      target: edge.target.ref,
      impacted: false,
      proposed: edge.status === 'proposed',
    });
  }
  for (const node of impact?.graph.nodes ?? []) {
    nodes.set(node.ref, { id: node.ref, label: node.label || node.ref, domain: node.domain, role: node.role });
  }
  for (const link of impact?.graph.links ?? []) {
    links.set(`${link.source}>${link.target}`, {
      source: link.source,
      target: link.target,
      impacted: link.impacted,
      proposed: link.status === 'proposed',
    });
  }
  if (nodes.size < 2 || links.size === 0) return null;
  // Keep the nodes the change touches first, then the rest, up to the cap.
  const rank = (n: SceneGraphNode) => (n.role === 'changed' ? 0 : n.role === 'idle' || n.role === 'unaffected' ? 2 : 1);
  const kept = [...nodes.values()].sort((a, b) => rank(a) - rank(b)).slice(0, MAX_SCENE_NODES);
  const ids = new Set(kept.map((n) => n.id));
  const keptLinks = [...links.values()].filter((l) => ids.has(l.source) && ids.has(l.target));
  if (keptLinks.length === 0) return null;
  return { nodes: kept, links: keptLinks };
}

export interface Placed extends SceneGraphNode {
  x: number;
  y: number;
}

/** Deterministic positions in the scene's 160 x 100 box: one column per domain. */
export function layoutGraph(graph: SceneGraph): Map<string, Placed> {
  const domains = [...new Set(graph.nodes.map((n) => n.domain))].sort(
    (a, b) => orderOf(a) - orderOf(b) || a.localeCompare(b),
  );
  const out = new Map<string, Placed>();
  domains.forEach((domain, col) => {
    const column = graph.nodes.filter((n) => n.domain === domain).sort((a, b) => a.id.localeCompare(b.id));
    const x = domains.length === 1 ? 80 : 20 + (120 * col) / (domains.length - 1);
    column.forEach((node, row) => {
      const y = column.length === 1 ? 50 : 18 + (64 * row) / (column.length - 1);
      out.set(node.id, { ...node, x: round(x), y: round(y) });
    });
  });
  return out;
}

function orderOf(domain: string): number {
  const i = DOMAIN_ORDER.indexOf(domain);
  return i === -1 ? DOMAIN_ORDER.length : i;
}

function round(v: number): number {
  return Math.round(v * 10) / 10;
}
