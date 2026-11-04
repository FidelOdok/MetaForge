/**
 * The sketch constraint solver (SKP-004, RC-03).
 *
 * Solves the **accepted** constraints and dimensions over the current
 * primitives by Levenberg-Marquardt least squares, starting from where the
 * primitives are now: a numeric dimension edit re-solves the existing
 * geometry and never re-runs recognition. Proposed and rejected constraints
 * are ignored.
 *
 * It then reports:
 *
 * - degrees of freedom: parameters minus the rank of the constraint
 *   Jacobian, and how many of those only move or turn the sketch as a whole
 *   (`shape_dof` is what is left to fix the shape and size);
 * - fully, under or over-constrained, in plain language;
 * - conflicts: when the accepted set cannot all hold, which constraints
 *   clash, and what would fix it (drop one, or relax a dimension to the
 *   value the others imply). Conflicting geometry is not moved;
 * - redundant constraints, already implied by others.
 *
 * Client side and dependency free by design; see the ADR in
 * `docs/twin_schema.md` (the sketch compiler section).
 */

import { constraintLabel, formatValue, joinNames, primitiveLabels } from '../describe';
import { DIMENSION_TYPES, type Conflict, type CorrectionChoice, type Primitive, type SketchConstraint, type SolveReport } from '../model';
import { levenbergMarquardt, rowRank } from './linalg';
import { ConstraintError, equationFor, layoutOf, pack, rigidMotions, unpack, type Equation, type Layout } from './equations';

export interface SolveOptions {
  /** Characteristic length for scaling angle residuals and tolerances. Default: the sketch extent. */
  scale?: number;
  maxIterations?: number;
  /** Most constraints to try dropping when explaining a conflict. */
  maxDropTests?: number;
}

export interface SolveResult {
  primitives: Primitive[];
  report: SolveReport;
  /** Accepted constraints that could not be applied (bad refs), with the reason. */
  invalid: { id: string; reason: string }[];
}

const now = (): number => (typeof performance !== 'undefined' ? performance.now() : Date.now());

/** Bounding-box diagonal of the primitives' defining points. */
export function sketchExtent(primitives: readonly Primitive[]): number {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const add = (x: number, y: number) => {
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x);
    maxY = Math.max(maxY, y);
  };
  for (const p of primitives) {
    if (p.type === 'line') {
      add(...p.start);
      add(...p.end);
    } else if (p.type === 'spline') {
      p.points.forEach((q) => add(...q));
    } else {
      const r = p.type === 'ellipse' ? p.major_radius : p.radius;
      add(p.center[0] - r, p.center[1] - r);
      add(p.center[0] + r, p.center[1] + r);
    }
  }
  return Number.isFinite(minX) ? Math.hypot(maxX - minX, maxY - minY) : 1;
}

interface System {
  layout: Layout;
  eqs: { id: string; fn: Equation; rows: number }[];
  invalid: { id: string; reason: string }[];
}

function buildSystem(primitives: readonly Primitive[], constraints: readonly SketchConstraint[], scale: number): System {
  const layout = layoutOf(primitives, scale);
  const eqs: System['eqs'] = [];
  const invalid: System['invalid'] = [];
  for (const c of constraints) {
    if (c.status !== 'accepted') continue;
    try {
      const fn = equationFor(c, layout);
      const rows = fn(pack(layout)).length;
      eqs.push({ id: c.id, fn, rows });
    } catch (err) {
      if (!(err instanceof ConstraintError)) throw err;
      invalid.push({ id: c.id, reason: err.message });
    }
  }
  return { layout, eqs, invalid };
}

function residualFn(eqs: System['eqs']): (x: Float64Array) => Float64Array {
  const m = eqs.reduce((s, e) => s + e.rows, 0);
  return (x) => {
    const out = new Float64Array(m);
    let k = 0;
    for (const e of eqs) for (const v of e.fn(x)) out[k++] = v;
    return out;
  };
}

/** Central-difference Jacobian: accurate enough to count rank reliably. */
function centralJacobian(f: (x: Float64Array) => Float64Array, x: Float64Array, m: number): Float64Array[] {
  const n = x.length;
  const J = Array.from({ length: m }, () => new Float64Array(n));
  const xp = Float64Array.from(x);
  for (let j = 0; j < n; j += 1) {
    const h = 1e-5 * Math.max(1, Math.abs(x[j]!));
    xp[j] = x[j]! + h;
    const rp = f(xp);
    xp[j] = x[j]! - h;
    const rm = f(xp);
    xp[j] = x[j]!;
    for (let i = 0; i < m; i += 1) J[i]![j] = (rp[i]! - rm[i]!) / (2 * h);
  }
  return J;
}

interface Core {
  x: Float64Array;
  converged: boolean;
  iterations: number;
  residuals: Record<string, number>;
  maxResidual: number;
}

function runCore(sys: System, tol: number, maxIterations: number): Core {
  const f = residualFn(sys.eqs);
  const x0 = pack(sys.layout);
  const lm = levenbergMarquardt(f, x0, { maxIterations, residualTol: tol * 1e-3, stepTol: tol * 1e-9 });
  const r = f(lm.x);
  const residuals: Record<string, number> = {};
  let k = 0;
  let maxResidual = 0;
  for (const e of sys.eqs) {
    let worst = 0;
    for (let i = 0; i < e.rows; i += 1) worst = Math.max(worst, Math.abs(r[k++]!));
    residuals[e.id] = worst;
    maxResidual = Math.max(maxResidual, worst);
  }
  return { x: lm.x, converged: maxResidual <= tol, iterations: lm.iterations, residuals, maxResidual };
}

/** Measured value of a dimension's quantity in a set of primitives. */
function measure(c: SketchConstraint, primitives: readonly Primitive[]): number | undefined {
  const p = primitives.find((q) => q.id === c.refs[0]);
  if (!p) return undefined;
  if (c.type === 'length' && p.type === 'line') return Math.hypot(p.end[0] - p.start[0], p.end[1] - p.start[1]);
  if (c.type === 'radius' && (p.type === 'arc' || p.type === 'circle')) return p.radius;
  return undefined;
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** Solve the accepted constraints over the primitives as they are now. */
export function solveSketch(
  primitives: readonly Primitive[],
  constraints: readonly SketchConstraint[],
  opts: SolveOptions = {},
): SolveResult {
  const t0 = now();
  const scale = opts.scale ?? Math.max(sketchExtent(primitives), 1e-9);
  const tol = 1e-7 * Math.max(scale, 1e-9);
  const maxIt = opts.maxIterations ?? 100;
  const labels = primitiveLabels(primitives);
  const byId = new Map(constraints.map((c) => [c.id, c]));
  const name = (id: string) => {
    const c = byId.get(id);
    return c ? constraintLabel(c, labels) : id;
  };

  const sys = buildSystem(primitives, constraints, scale);
  const core = runCore(sys, tol, maxIt);
  const m = sys.eqs.reduce((s, e) => s + e.rows, 0);
  const rowOwner: string[] = sys.eqs.flatMap((e) => Array<string>(e.rows).fill(e.id));

  const f = residualFn(sys.eqs);
  const xAt = core.converged ? core.x : pack(sys.layout);
  const J = m > 0 ? centralJacobian(f, xAt, m) : [];
  const rank = rowRank(J, 1e-6);
  const n = sys.layout.size;
  // Construction geometry (a symmetry axis) is a helper: its own freedom
  // (how long the axis is drawn, where along itself it sits) is not the
  // shape's. Counting constraints on the real geometry only, after
  // eliminating the helpers' parameters: rank(J) - rank(J restricted to them).
  const helperCols: number[] = [];
  for (const { prim, offset, size } of sys.layout.slots.values()) {
    if (prim.construction) for (let k = 0; k < size; k += 1) helperCols.push(offset + k);
  }
  const helperRank =
    helperCols.length && m > 0 ? rowRank(J.map((row) => Float64Array.from(helperCols, (c) => row[c]!)), 1e-6).rank : 0;
  const dof = n - helperCols.length - (rank.rank - helperRank);
  // How many rigid motions (move u, move v, turn) are still free.
  const G = rigidMotions(sys.layout, xAt);
  const JG = G.map((g) => {
    const col = new Float64Array(m);
    for (let i = 0; i < m; i += 1) {
      let s = 0;
      const row = J[i]!;
      for (let j = 0; j < n; j += 1) s += row[j]! * g[j]!;
      col[i] = s;
    }
    return col;
  });
  const rigidFree = n === 0 ? 0 : rowRank(G, 1e-9).rank - (m > 0 ? rowRank(JG, 1e-6).rank : 0);
  const shapeDof = Math.max(0, dof - rigidFree);

  const conflicts: Conflict[] = [];
  const redundantSet = new Set<string>();
  let status: SolveReport['status'];
  let message: string;
  let solved: Primitive[];

  if (core.converged) {
    for (const d of rank.dependent) redundantSet.add(rowOwner[d.row]!);
    solved = unpack(sys.layout, core.x);
    if (shapeDof === 0) {
      status = 'fully';
      message =
        rigidFree > 0
          ? 'The shape and size are fully fixed. It can still move on its plane until it is placed.'
          : 'The shape, size and position are fully fixed.';
    } else {
      status = 'under';
      message = `${plural(shapeDof, 'more dimension or rule', 'more dimensions or rules')} would fix the shape and size.`;
    }
  } else {
    status = 'over';
    solved = primitives.map((p) => ({ ...p }));
    // Candidates: constraints in a dependent group, then those left unsatisfied.
    const candidates: string[] = [];
    const push = (id: string) => {
      if (!candidates.includes(id)) candidates.push(id);
    };
    for (const d of rank.dependent) for (const row of d.group) push(rowOwner[row]!);
    for (const [id, r] of Object.entries(core.residuals)) if (r > tol) push(id);
    if (candidates.length === 0) for (const e of sys.eqs) push(e.id);
    const limit = opts.maxDropTests ?? 16;
    const choices: CorrectionChoice[] = [];
    const clash: string[] = [];
    for (const id of candidates.slice(0, limit)) {
      const rest = constraints.map((c) => (c.id === id ? { ...c, status: 'rejected' as const } : c));
      const trial = runCore(buildSystem(primitives, rest, scale), tol, maxIt);
      if (!trial.converged) continue;
      clash.push(id);
      const c = byId.get(id)!;
      choices.push({ kind: 'drop', constraint_id: id, label: `Remove "${name(id)}"` });
      if (DIMENSION_TYPES.includes(c.type)) {
        const value = measure(c, unpack(buildSystem(primitives, rest, scale).layout, trial.x));
        if (value !== undefined && value > 0) {
          choices.push({ kind: 'relax', constraint_id: id, value, label: `Change it to ${formatValue(value)}` });
        }
      }
    }
    const ids = clash.length ? clash : candidates.slice(0, limit);
    // Lead with the sizes when sizes are part of the clash: "these two
    // lengths disagree" is the sentence a person can act on. The shape rules
    // that also take part stay in `constraint_ids` for the detail view.
    const dimsInClash = clash.filter((id) => DIMENSION_TYPES.includes(byId.get(id)!.type));
    const lead = dimsInClash.length ? dimsInClash : ids;
    const others = ids.length - lead.length;
    const names = lead.map(name);
    const head = lead.length === 2 ? 'These two rules conflict' : 'These rules conflict';
    const tail = others > 0 ? ` (${plural(others, 'shape rule is', 'shape rules are')} involved too)` : '';
    message = clash.length
      ? `${head}: ${joinNames(names)}${tail}. Remove one, or change a dimension.`
      : `The accepted rules cannot all hold: ${joinNames(names)}. Remove some of them and try again.`;
    if (dimsInClash.length) {
      const keep = new Set(dimsInClash);
      choices.splice(0, choices.length, ...choices.filter((c) => keep.has(c.constraint_id)));
    }
    conflicts.push({ constraint_ids: ids, message, choices });
  }

  const duration = Math.round((now() - t0) * 100) / 100;
  return {
    primitives: solved,
    invalid: sys.invalid,
    report: {
      status,
      dof,
      shape_dof: shapeDof,
      converged: core.converged,
      iterations: core.iterations,
      max_residual: core.maxResidual,
      residuals: core.residuals,
      conflicts,
      redundant: [...redundantSet],
      message,
      duration_ms: duration,
    },
  };
}
