import type { TwinNode } from '../../types/twin';
import { geometryBadgeWord } from '../geometry/GeometryStatusBadge';
import { is3dEngine, previewEngineFor } from '../preview/registry';

/**
 * What the Create stage says about the artifact it shows, and the one next
 * action it offers. Plain functions, so every rule is tested on its own.
 */

/** The four maturity words Create uses (16.2 item 3). */
export type Maturity = 'Illustrative' | 'Preview' | 'Exact' | 'Validated';

export const CONCEPT_MESH_TOOL = 'twin.generate_concept_mesh';
export const NOT_ASSESSED = 'Manufacturing not assessed';

const ENGINEERING_TYPES = new Set(['cad_model', 'pcb_layout']);

function prop(node: TwinNode, key: string): string {
  return String(node.properties?.[key] ?? '');
}

/** An AI 3D concept (L0): a GLB made by the concept mesh tool, illustrative. */
export function isConceptMesh(node: TwinNode): boolean {
  return prop(node, 'generated_by') === CONCEPT_MESH_TOOL;
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
  if (isConceptMesh(node)) return { maturity: 'Illustrative', status: '3D Concept · illustrative', kind: 'concept3d' };
  const { engine } = previewEngineFor(node);
  if (is3dEngine(engine) && prop(node, 'authority') !== 'illustrative' && wpType !== 'image' && wpType !== 'design_sketch') {
    return { maturity: 'Preview', status: '3D Preview · provisional', kind: 'preview' };
  }
  if (is3dEngine(engine)) return { maturity: 'Illustrative', status: '3D Concept · illustrative', kind: 'concept3d' };
  return { maturity: 'Illustrative', status: 'Concept · illustrative', kind: 'concept' };
}

/** The single primary next action (UXS-002, 16.2 item 5). `start` offers three equal entries. */
export type PrimaryAction = 'start' | 'concept_3d' | 'make_editable' | 'build_exact' | 'prepare_prototype';

export const PRIMARY_LABEL: Record<Exclude<PrimaryAction, 'start'>, string> = {
  concept_3d: 'Make a 3D concept',
  make_editable: 'Make editable',
  build_exact: 'Build exact model',
  prepare_prototype: 'Prepare prototype',
};

/** The three equivalent ways to start (16.2 item 1). */
export const START_ACTIONS = ['Describe', 'Sketch it', 'Add reference'] as const;

export interface StageFacts {
  /** The brief has something in it, or the conversation has begun. */
  described: boolean;
  /** An accepted generated concept, a concept image or a concept render. */
  hasConcept: boolean;
  /** An AI 3D concept exists. */
  hasConcept3d: boolean;
  /** Strokes on the sketch layer. */
  hasSketch: boolean;
  /** The sketch was made editable (shapes, sizes and rules). */
  hasEditable: boolean;
  hasReferences: boolean;
  /** A dimensioned model (CAD or PCB). */
  hasEngineering: boolean;
}

export function primaryActionFor(f: StageFacts): PrimaryAction {
  if (f.hasEngineering) return 'prepare_prototype';
  const begun = f.described || f.hasConcept || f.hasConcept3d || f.hasSketch || f.hasReferences;
  if (!begun) return 'start';
  if (!f.hasConcept3d) return 'concept_3d';
  if (f.hasSketch && !f.hasEditable) return 'make_editable';
  return 'build_exact';
}
