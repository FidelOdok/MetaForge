/**
 * Which twin edges the graph can draw, and which it is hiding (FORGE-704).
 *
 * The relationships view lists work products, but an edge can run to a
 * constraint, a baseline, a BOM item or an engineering entity. The graph used to
 * keep only edges whose two ends were both visible work products, so on the
 * sandbox 412 of 641 edges disappeared with no message. Here every edge is
 * accounted for: drawn, hidden by a switch the person controls, or off screen
 * because its work product is filtered out of the list.
 */

import type { TwinEndpoint, TwinRelationship } from '../types/twin';

export type LinkGroup = 'constraints' | 'intent' | 'parts' | 'baselines' | 'other';

export const LINK_GROUPS: readonly { id: LinkGroup; label: string }[] = [
  { id: 'constraints', label: 'Constraints' },
  { id: 'intent', label: 'Intent & evidence' },
  { id: 'parts', label: 'Parts' },
  { id: 'baselines', label: 'Baselines' },
  { id: 'other', label: 'Other' },
];

/** Baseline membership is one edge per member and drowns the rest, so it starts off. */
export const DEFAULT_SHOWN: ReadonlySet<LinkGroup> = new Set<LinkGroup>([
  'constraints',
  'intent',
  'parts',
  'other',
]);

const INTENT_KINDS = new Set([
  'engineering_entity',
  'intent',
  'stakeholder_need',
  'objective',
  'assumption',
  'question',
  'risk',
  'verification_case',
  'evidence',
  'budget',
  'invariant',
  'waiver',
  'decision',
  'claim',
]);
const PART_KINDS = new Set(['bom_item', 'component', 'device_instance', 'datasheet']);

export function groupOf(kind: string): LinkGroup {
  if (kind === 'constraint') return 'constraints';
  if (kind === 'baseline') return 'baselines';
  if (PART_KINDS.has(kind)) return 'parts';
  if (INTENT_KINDS.has(kind)) return 'intent';
  return 'other';
}

export interface LinkSplit {
  /** Edges to hand to the canvas: both ends visible, or one visible work product and a shown far end. */
  drawn: TwinRelationship[];
  /** The far ends of the drawn edges that are not work products, to draw as lighter nodes. */
  ghosts: Record<string, TwinEndpoint>;
  /** Edges from a visible work product to each group, whether shown or not (for the switches). */
  counts: Record<LinkGroup, number>;
  /** Edges not drawn because their far end belongs to a switched-off group. */
  hidden: number;
  /**
   * Edges that cannot be drawn because an end is not on screen and is not named: a
   * work product filtered out of the list (or an unapproved draft), or an end an
   * older gateway did not name.
   */
  offscreen: number;
}

const emptyCounts = (): Record<LinkGroup, number> => ({
  constraints: 0,
  intent: 0,
  parts: 0,
  baselines: 0,
  other: 0,
});

export function splitLinks(args: {
  relationships: TwinRelationship[];
  endpoints: Record<string, TwinEndpoint>;
  visibleIds: ReadonlySet<string>;
  shown: ReadonlySet<LinkGroup>;
}): LinkSplit {
  const { relationships, endpoints, visibleIds, shown } = args;
  const drawn: TwinRelationship[] = [];
  const ghosts: Record<string, TwinEndpoint> = {};
  const counts = emptyCounts();
  let hidden = 0;
  let offscreen = 0;

  for (const edge of relationships) {
    const sourceVisible = visibleIds.has(edge.sourceId);
    const targetVisible = visibleIds.has(edge.targetId);

    if (sourceVisible && targetVisible) {
      drawn.push(edge);
      continue;
    }
    // One end must be a work product on screen; the other is what the edge is about.
    const farId = sourceVisible ? edge.targetId : targetVisible ? edge.sourceId : null;
    const far = farId ? endpoints[farId] : undefined;
    if (!farId || !far) {
      offscreen += 1;
      continue;
    }
    const group = groupOf(far.kind);
    counts[group] += 1;
    if (shown.has(group)) {
      drawn.push(edge);
      ghosts[farId] = far;
    } else {
      hidden += 1;
    }
  }
  return { drawn, ghosts, counts, hidden, offscreen };
}
