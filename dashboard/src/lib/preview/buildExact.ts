/**
 * Build exact model, from Create (spec 16.2 items 5 and 8).
 *
 * Before the exact build of a parametric sketch starts, every input only a
 * person can give (`required_input`: the depth or the turn angle, and the
 * material) must be known. This decides, from the preview definition, what
 * happens next:
 *
 * - **blocked**: something must be done first (approve the sketch, place it
 *   on a plane in millimetres), said plainly with where to fix it;
 * - **ask**: one concise question for the first missing input, with concrete
 *   choices drawn from the sketch itself or the manufacturing context, and a
 *   field for another value. Nothing is ever filled in without the person;
 * - **ready**: every input is known; the build can start with the answers.
 *
 * Pure, so each case is tested without the network.
 */

import type { PlaneElement, PreviewDefinition } from './definition';

export interface ExactChoice {
  label: string;
  value: number | string;
}

export interface ExactQuestion {
  /** The feature parameter it answers: `depth`, `angle` or `material`. */
  parameter: string;
  prompt: string;
  choices: ExactChoice[];
  /** The field for another value. */
  input: { kind: 'number' | 'text'; label: string; unit: string | null; min?: number; max?: number };
}

export type ExactBuildPlan =
  | { kind: 'blocked'; message: string; fix: 'editable' | 'plane' | null }
  | { kind: 'ask'; question: ExactQuestion }
  | { kind: 'ready'; values: Record<string, number | string> };

/** What the manufacturing context names as the material, or its candidates. */
export interface MaterialHint {
  name: string;
  /** A person stated it (or a decision did), rather than it being a candidate. */
  stated: boolean;
}

const ORDER = ['depth', 'angle', 'material'];

function round1(v: number): number {
  return Math.round(v * 10) / 10;
}

function fmt(v: number): string {
  return Number.isInteger(v) ? String(v) : String(round1(v));
}

/** The profile's width and height on the plane, in the sketch's units. */
export function profileExtent(elements: PlaneElement[]): { width: number; height: number } | null {
  let minU = Infinity;
  let minV = Infinity;
  let maxU = -Infinity;
  let maxV = -Infinity;
  const add = (u: number, v: number) => {
    minU = Math.min(minU, u);
    maxU = Math.max(maxU, u);
    minV = Math.min(minV, v);
    maxV = Math.max(maxV, v);
  };
  for (const el of elements) {
    if (el.construction) continue;
    switch (el.type) {
      case 'line':
        add(...el.start);
        add(...el.end);
        break;
      case 'circle':
      case 'arc':
        add(el.center[0] - el.radius, el.center[1] - el.radius);
        add(el.center[0] + el.radius, el.center[1] + el.radius);
        break;
      case 'ellipse':
        add(el.center[0] - el.major_radius, el.center[1] - el.major_radius);
        add(el.center[0] + el.major_radius, el.center[1] + el.major_radius);
        break;
      case 'rectangle':
        add(el.origin[0], el.origin[1]);
        add(el.origin[0] + el.width, el.origin[1] + el.height);
        break;
      case 'spline':
        el.points.forEach((p) => add(...p));
        break;
    }
  }
  if (!Number.isFinite(minU)) return null;
  return { width: maxU - minU, height: maxV - minV };
}

function depthChoices(def: PreviewDefinition, unit: string): ExactChoice[] {
  const ext = profileExtent(def.elements);
  if (!ext) return [];
  const out: ExactChoice[] = [];
  const add = (label: string, v: number) => {
    const value = round1(v);
    if (value > 0 && !out.some((c) => c.value === value)) out.push({ label: `${label} (${fmt(value)} ${unit})`, value });
  };
  const small = Math.min(ext.width, ext.height);
  const large = Math.max(ext.width, ext.height);
  add('A tenth of its width', large / 10);
  add('A quarter of its width', large / 4);
  add('As deep as it is narrow', small);
  return out;
}

const ANGLE_CHOICES: ExactChoice[] = [
  { label: 'A full turn (360°)', value: 360 },
  { label: 'Half a turn (180°)', value: 180 },
  { label: 'A quarter turn (90°)', value: 90 },
];

function questionFor(def: PreviewDefinition, name: string, materials: MaterialHint[]): ExactQuestion {
  const param = def.parameters[name];
  const unitText = def.unit === 'unknown' ? 'units' : def.unit;
  if (name === 'depth') {
    return {
      parameter: name,
      prompt: param?.question || 'How thick should this be?',
      choices: depthChoices(def, unitText),
      input: { kind: 'number', label: `Another thickness (${unitText})`, unit: unitText, min: 0.01 },
    };
  }
  if (name === 'angle') {
    return {
      parameter: name,
      prompt: param?.question || 'How far round should it turn?',
      choices: ANGLE_CHOICES,
      input: { kind: 'number', label: 'Another angle (degrees)', unit: 'deg', min: 1, max: 360 },
    };
  }
  if (name === 'material') {
    return {
      parameter: name,
      prompt: param?.question || 'What material is it made of?',
      choices: materials.map((m) => ({
        label: m.stated ? `${m.name} (from your manufacturing notes)` : `${m.name} (a candidate in your manufacturing notes)`,
        value: m.name,
      })),
      input: { kind: 'text', label: 'Another material', unit: null },
    };
  }
  return {
    parameter: name,
    prompt: param?.question || `What should ${name} be?`,
    choices: [],
    input: { kind: param?.unit ? 'number' : 'text', label: name, unit: param?.unit ?? null },
  };
}

/** What Build exact model does next for this definition and the answers given so far. */
export function exactBuildPlan(
  def: PreviewDefinition,
  answers: Record<string, number | string> = {},
  materials: MaterialHint[] = [],
): ExactBuildPlan {
  if (!def.approved) {
    return {
      kind: 'blocked',
      message: 'Approve the editable sketch first: the exact model is only built from an approved sketch.',
      fix: 'editable',
    };
  }
  if (def.plane_blocked) {
    return { kind: 'blocked', message: `The sketch's plane needs attention first: ${def.plane_blocked}`, fix: 'plane' };
  }
  if (def.unit !== 'mm' || def.plane_assumed) {
    return {
      kind: 'blocked',
      message: 'Place the sketch on a plane in millimetres first, so the exact model has a real size and position.',
      fix: 'plane',
    };
  }
  const missing = [...def.missing]
    .filter((n) => answers[n] === undefined || answers[n] === '')
    .sort((a, b) => (ORDER.indexOf(a) + 1 || 99) - (ORDER.indexOf(b) + 1 || 99));
  const next = missing[0];
  if (next) return { kind: 'ask', question: questionFor(def, next, materials) };
  const values: Record<string, number | string> = {};
  for (const n of def.missing) if (answers[n] !== undefined) values[n] = answers[n]!;
  return { kind: 'ready', values };
}
