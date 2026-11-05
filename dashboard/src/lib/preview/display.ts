import type { ComparisonRecord, PreviewDefinition } from './definition';

/**
 * What the stage shows for a parametric component, and the words it uses
 * (spec section 10, SYNC-005, SYNC-006, SYNC-009, 3D-RC-006). Pure.
 *
 * - While the person edits, or the exact build runs, the L2 preview is on
 *   screen: **3D Preview · provisional**.
 * - When the validated exact revision built from *this* definition arrives,
 *   the stage swaps to its tessellation (L3): **Engineering Geometry · exact
 *   model available**, with a short note when the comparison found a
 *   discrepancy.
 * - When the exact build fails, the preview and the last valid model stay,
 *   and it says **Preview available · Engineering update needs attention**,
 *   with a retry. Nothing claims exact.
 */

export const STATUS_PREVIEW = '3D Preview · provisional';
export const STATUS_EXACT = 'Engineering Geometry · exact model available';
export const STATUS_PENDING = 'Engineering Update · validation pending';
export const STATUS_ATTENTION = 'Preview available · Engineering update needs attention';

const VALIDATED = new Set(['validated', 'released']);
const RUNNING = new Set(['queued', 'running']);

export interface DisplayInputs {
  /** The browser holds edits the gateway has not seen. */
  dirty: boolean;
  /** A parameter commit is on its way. */
  committing: boolean;
  /** The commit itself was refused (a stale base, a broken rule). */
  commitError: string | null;
}

export interface ParametricDisplay {
  /** Which representation is the main one on screen. */
  show: 'l2' | 'l3';
  /** The validated exact revision to draw: the main model after the swap, else kept faintly under the preview. */
  l3NodeId: string | null;
  l3Ref: string | null;
  maturity: 'Preview' | 'Validated';
  status: string;
  /** A second line: the engineering update in flight. */
  secondary: string | null;
  /** Short notes: assumptions, a discrepancy. */
  notes: string[];
  attention: boolean;
  pending: boolean;
  /** The failed operation to retry (`job:<id>`). */
  retryOperation: string | null;
}

const CHECK_WORDS: Record<string, string> = {
  bounding_box: 'overall size',
  dimensions: 'main dimensions',
  volume: 'volume',
  surface_distance: 'surface shape',
  features: 'holes',
};

/** A plain-language note for Create when the comparison found a discrepancy. */
export function discrepancyNote(c: ComparisonRecord | null | undefined): string | null {
  if (!c || c.outcome !== 'discrepancy' || !c.discrepancies.length) return null;
  const words = [...new Set(c.discrepancies.map((d) => CHECK_WORDS[d.check] ?? d.check))];
  return `The exact model differs from the preview in ${words.join(', ')}.`;
}

/** Notes for what the preview had to assume (never sent to the exact path). */
export function assumptionNotes(def: PreviewDefinition): string[] {
  const out: string[] = [];
  for (const [name, p] of Object.entries(def.parameters)) {
    if (p.assumed && p.value != null) {
      const unit = p.unit && p.unit !== 'unknown' ? ` ${p.unit}` : '';
      out.push(`${name.charAt(0).toUpperCase()}${name.slice(1)} not set · showing an assumed ${p.value}${unit}`);
    }
  }
  if (def.scale_assumed) out.push('Scale not confirmed · drawn at an assumed 1 sketch unit = 1 mm');
  else if (def.unit === 'in') out.push('Drawn in inches, shown in millimetres');
  if (def.plane_blocked) out.push('The sketch plane needs reattaching · drawn on XY');
  else if (def.plane_assumed) out.push('No plane placed · drawn on XY (assumed)');
  return out;
}

export function parametricDisplay(def: PreviewDefinition, ui: DisplayInputs): ParametricDisplay {
  const cad = def.cad;
  const build = def.build;
  const lastValid = cad.last_validated;
  const exactHere = cad.matches_definition && VALIDATED.has(String(cad.geometry_state));
  const jobFailed = build?.status === 'failed';
  const jobRunning = !!build && RUNNING.has(build.status);
  // After a reload there is no job id: a failed attempt on the head that is
  // not this definition's still needs attention.
  const attemptFailed =
    !build && !exactHere && def.buildable && cad.latest_attempt?.state === 'failed' && !!cad.latest_attempt.job_id;
  const notes = assumptionNotes(def);
  const base = {
    l3NodeId: lastValid?.node_id ?? null,
    l3Ref: lastValid?.item_ref ?? null,
    notes,
    retryOperation: null as string | null,
  };
  if (ui.commitError) {
    return {
      ...base,
      show: 'l2',
      maturity: 'Preview',
      status: STATUS_PREVIEW,
      secondary: null,
      notes: [ui.commitError, ...notes],
      attention: false,
      pending: false,
    };
  }
  if (jobFailed || attemptFailed) {
    return {
      ...base,
      show: 'l2',
      maturity: 'Preview',
      status: STATUS_ATTENTION,
      secondary: lastValid ? `Last valid model kept: ${lastValid.item_ref}` : 'No valid exact model yet',
      attention: true,
      pending: false,
      retryOperation: jobFailed ? (build?.operation_id ?? `job:${build?.job_id}`) : `job:${cad.latest_attempt?.job_id}`,
    };
  }
  if (ui.dirty || ui.committing) {
    return { ...base, show: 'l2', maturity: 'Preview', status: STATUS_PREVIEW, secondary: null, attention: false, pending: ui.committing };
  }
  if (jobRunning) {
    return { ...base, show: 'l2', maturity: 'Preview', status: STATUS_PREVIEW, secondary: STATUS_PENDING, attention: false, pending: true };
  }
  if (exactHere) {
    const note = discrepancyNote(cad.comparison);
    return {
      ...base,
      show: 'l3',
      l3NodeId: cad.node_id,
      l3Ref: cad.head_revision != null ? `${cad.item_key}@${cad.head_revision}` : base.l3Ref,
      maturity: 'Validated',
      status: STATUS_EXACT,
      secondary: null,
      notes: note ? [note] : [],
      attention: false,
      pending: false,
    };
  }
  return { ...base, show: 'l2', maturity: 'Preview', status: STATUS_PREVIEW, secondary: null, attention: false, pending: false };
}
