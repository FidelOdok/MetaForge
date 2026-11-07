import type { TwinNode } from '../../types/twin';
import { geometryBadgeWord } from '../geometry/GeometryStatusBadge';
import { guardedLabel, representationText } from '../geometry/RepresentationBadge';
import { is3dEngine, previewEngineFor } from '../preview/registry';

/**
 * What the Create stage says about the artifact it shows, and the one next
 * action it offers. Plain functions, so every rule is tested on its own.
 */

/** The four maturity words Create uses (16.2 item 3). */
export type Maturity = 'Illustrative' | 'Preview' | 'Exact' | 'Validated';

export const CONCEPT_MESH_TOOL = 'twin.generate_concept_mesh';
export const STRUCTURED_MESH_TOOL = 'twin.render_structured_mesh';
/** The tools that write a new revision of a 3D concept (concept parts). */
export const CONCEPT_REVISION_TOOLS = new Set([
  CONCEPT_MESH_TOOL,
  'twin.segment_concept_mesh',
  'twin.regenerate_concept_part',
  'twin.complete_concept_part',
]);
export const NOT_ASSESSED = 'Manufacturing not assessed';

const ENGINEERING_TYPES = new Set(['cad_model', 'pcb_layout']);

function prop(node: TwinNode, key: string): string {
  return String(node.properties?.[key] ?? '');
}

/** An AI 3D concept (L0): a GLB made by the concept mesh tool, illustrative. */
export function isConceptMesh(node: TwinNode): boolean {
  return CONCEPT_REVISION_TOOLS.has(prop(node, 'generated_by'));
}

/**
 * A 3D concept revision nobody has accepted (a proposal, or one rejected or
 * superseded): it never takes the stage on its own, so the accepted concept
 * stays until the person chooses the new one.
 */
export function isPendingConceptRevision(node: TwinNode): boolean {
  const status = prop(node, 'concept_status');
  return isConceptMesh(node) && (status === 'proposed' || status === 'rejected' || status === 'superseded');
}

/** Simple 3D parts (L1): the concept's block layout, one node per component. */
export function isStructuredMesh(node: TwinNode): boolean {
  return node.representation?.level === 'L1' || prop(node, 'generated_by') === STRUCTURED_MESH_TOOL;
}

/**
 * Words an illustrative or provisional item may never carry (3D-RC-012).
 * "Not a manufacturable model" stays allowed: it says the opposite.
 */
const OVERCLAIM = /\b(verified|validated|production[- ]ready|ready for production|certified|approved for (?:production|manufacture))\b/i;

/** Whether a label overclaims for an item of this maturity. */
export function overclaims(text: string, maturity: Maturity): boolean {
  return (maturity === 'Illustrative' || maturity === 'Preview') && OVERCLAIM.test(text);
}

/**
 * The label guard: an illustrative or provisional item never shows a
 * verified or production-ready claim. An overclaiming text is replaced by
 * the plain fallback rather than shown.
 */
export function truthfulLabel(text: string, maturity: Maturity, fallback: string): string {
  return overclaims(text, maturity) ? fallback : text;
}

/**
 * A concept's short manufacturing note, when one was assessed; otherwise
 * "Manufacturing not assessed". Guarded like every illustrative label.
 */
export function manufacturingNote(note: unknown): string {
  const text = typeof note === 'string' ? note.trim() : '';
  if (!text) return NOT_ASSESSED;
  return truthfulLabel(text, 'Illustrative', NOT_ASSESSED);
}

export interface Representation {
  maturity: Maturity;
  /** The compact status line under the maturity (section 10). */
  status: string;
  kind: 'concept' | 'concept3d' | 'preview' | 'engineering';
}

/** The maturity and status line of the artifact on the stage. */
export function representationOf(node: TwinNode, checks?: 'running' | 'failed' | null): Representation {
  const rep = rawRepresentationOf(node, checks);
  // The label guard: whatever the record says, an L0, L1 or picture is never
  // shown as exact or validated.
  const level = node.representation?.level;
  if (!level) return rep;
  const maturity = guardedLabel(level, rep.maturity) as Maturity;
  return maturity === rep.maturity ? rep : { ...rep, maturity, status: truthfulLabel(rep.status, maturity, 'Concept · illustrative') };
}

function rawRepresentationOf(node: TwinNode, checks?: 'running' | 'failed' | null): Representation {
  const wpType = prop(node, 'wp_type');
  if (ENGINEERING_TYPES.has(wpType)) {
    const state = node.geometryState?.state ?? null;
    if (checks === 'failed' || state === 'failed') {
      return { maturity: 'Preview', status: 'Preview available · Engineering update needs attention', kind: 'engineering' };
    }
    const word = geometryBadgeWord(state);
    if (word === 'Concept') return { maturity: 'Illustrative', status: 'Concept · illustrative', kind: 'engineering' };
    if (word === 'Preview') return { maturity: 'Preview', status: '3D Preview · provisional', kind: 'engineering' };
    if (checks === 'running' || state === 'validation_pending') {
      return { maturity: 'Exact', status: 'Engineering Update · validation pending', kind: 'engineering' };
    }
    if (word === 'Validated') return { maturity: 'Validated', status: 'Engineering Geometry · validated', kind: 'engineering' };
    return { maturity: 'Exact', status: 'Engineering Geometry · exact model available', kind: 'engineering' };
  }
  if (isStructuredMesh(node)) return { maturity: 'Preview', status: '3D Preview · simple parts, sizes estimated', kind: 'preview' };
  if (isConceptMesh(node) || node.representation?.level === 'L0') {
    return { maturity: 'Illustrative', status: '3D Concept · illustrative', kind: 'concept3d' };
  }
  if (node.representation?.level === 'illustration_image') {
    return { maturity: 'Illustrative', status: 'Concept · illustrative', kind: 'concept' };
  }
  const { engine } = previewEngineFor(node);
  if (is3dEngine(engine) && prop(node, 'authority') !== 'illustrative' && wpType !== 'image' && wpType !== 'design_sketch') {
    return { maturity: 'Preview', status: '3D Preview · provisional', kind: 'preview' };
  }
  if (is3dEngine(engine)) return { maturity: 'Illustrative', status: '3D Concept · illustrative', kind: 'concept3d' };
  return { maturity: 'Illustrative', status: 'Concept · illustrative', kind: 'concept' };
}

/** The level line under the maturity: "L1 · simple parts, sizes estimated · from SKT-1@2". */
export function levelText(node: TwinNode): string | null {
  return representationText(node.representation);
}

const ENGINEERING_RANK = 4;

/**
 * How much the stage wants to show a node (SYNC-010, 16.2 item 3): L3, then
 * L2, then L1, then L0, then a picture. A revision that is out of date ranks
 * last. Older records without a representation fall back to their type.
 */
export function displayRank(node: TwinNode): number {
  if (node.geometryState?.state === 'stale') return 0;
  const rank = node.representation?.display_rank;
  if (typeof rank === 'number' && rank > 0) return rank;
  if (ENGINEERING_TYPES.has(prop(node, 'wp_type'))) {
    const state = node.geometryState?.state;
    return state === 'validated' || state === 'released' ? 5 : ENGINEERING_RANK;
  }
  if (isStructuredMesh(node)) return 3;
  if (isConceptMesh(node) || is3dEngine(previewEngineFor(node).engine)) return 2;
  return 1;
}

/**
 * Only each item's current revision: when several revisions of one item are
 * listed, the highest one stands for it.
 */
export function currentRevisions(nodes: TwinNode[]): TwinNode[] {
  const best = new Map<string, TwinNode>();
  const out: TwinNode[] = [];
  for (const n of nodes) {
    const key = prop(n, 'item_key');
    if (!key) {
      out.push(n);
      continue;
    }
    const prior = best.get(key);
    if (!prior || Number(n.properties?.item_revision ?? 0) > Number(prior.properties?.item_revision ?? 0)) best.set(key, n);
  }
  return [...out, ...best.values()];
}

/** The best real representation of the current revision: L3, L2, L1, L0, then a picture. */
export function preferredDisplayNode(nodes: TwinNode[]): TwinNode | undefined {
  return [...currentRevisions(nodes.filter((n) => !isPendingConceptRevision(n)))].sort(
    (a, b) => displayRank(b) - displayRank(a) || (b.updatedAt ?? '').localeCompare(a.updatedAt ?? ''),
  )[0];
}

/** The CAD item a parametric sketch builds into: `PSK-X` -> `CAD-X` (the gateway's rule). */
export function cadKeyForSketch(sketchKey: string): string {
  const stem = sketchKey.includes('-') ? sketchKey.slice(sketchKey.indexOf('-') + 1) : sketchKey;
  return `CAD-${stem}`.slice(0, 80);
}

/**
 * The project's newest parametric sketch (C3D slice 5): when there is one,
 * the stage shows its L2 preview, swapping to the validated exact model
 * built from it.
 */
export function latestParametricSketch(nodes: TwinNode[]): { key: string; nodeId: string } | null {
  const sketches = currentRevisions(nodes.filter((n) => prop(n, 'wp_type') === 'parametric_sketch' && prop(n, 'item_key')));
  const newest = sketches.sort((a, b) => (b.updatedAt ?? '').localeCompare(a.updatedAt ?? ''))[0];
  return newest ? { key: prop(newest, 'item_key'), nodeId: newest.id } : null;
}

/** Develop's one primary action (UXS-002): what moves the twin's model forward next. */
export type DevelopAction = 'build_exact' | 'reconstruct' | 'request_exact' | 'describe_change';

export const DEVELOP_LABEL: Record<DevelopAction, string> = {
  build_exact: 'Build exact model',
  reconstruct: 'Make it editable',
  request_exact: 'Ask for the exact model',
  describe_change: 'Describe a change',
};

export interface DevelopFacts {
  /** A dimensioned model (CAD or PCB) exists. */
  hasEngineering: boolean;
  /** A parametric sketch exists, so the exact model can be built from it. */
  hasParametricSketch: boolean;
  /** A concept was accepted (its parts are recorded): it can be made editable (spec 4B). */
  conceptAccepted: boolean;
  /** The accepted concept's proposed features were accepted. */
  editableFromConcept: boolean;
}

export function developPrimary(f: DevelopFacts): DevelopAction {
  if (f.hasEngineering) return 'describe_change';
  if (f.hasParametricSketch) return 'build_exact';
  if (f.conceptAccepted && !f.editableFromConcept) return 'reconstruct';
  return 'request_exact';
}
