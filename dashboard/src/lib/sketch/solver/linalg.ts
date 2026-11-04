/**
 * Small dense linear algebra for the sketch solver and the fitters.
 *
 * Sketches here are bounded (tens of primitives, so under a few hundred
 * parameters), which keeps dense O(n^3) work far inside the time budget.
 * Matrices are plain row-major `Float64Array`s.
 */

/** Solve `A x = b` (n by n, row-major) by Gaussian elimination with partial pivoting. */
export function solveLinear(A: Float64Array, b: Float64Array, n: number): Float64Array | null {
  const M = Float64Array.from(A);
  const x = Float64Array.from(b);
  for (let col = 0; col < n; col += 1) {
    let piv = col;
    let best = Math.abs(M[col * n + col]!);
    for (let r = col + 1; r < n; r += 1) {
      const v = Math.abs(M[r * n + col]!);
      if (v > best) {
        best = v;
        piv = r;
      }
    }
    if (best < 1e-300) return null;
    if (piv !== col) {
      for (let c = 0; c < n; c += 1) {
        const t = M[col * n + c]!;
        M[col * n + c] = M[piv * n + c]!;
        M[piv * n + c] = t;
      }
      const t = x[col]!;
      x[col] = x[piv]!;
      x[piv] = t;
    }
    const d = M[col * n + col]!;
    for (let r = col + 1; r < n; r += 1) {
      const f = M[r * n + col]! / d;
      if (f === 0) continue;
      for (let c = col; c < n; c += 1) M[r * n + c] = M[r * n + c]! - f * M[col * n + c]!;
      x[r] = x[r]! - f * x[col]!;
    }
  }
  for (let r = n - 1; r >= 0; r -= 1) {
    let s = x[r]!;
    for (let c = r + 1; c < n; c += 1) s -= M[r * n + c]! * x[c]!;
    x[r] = s / M[r * n + r]!;
  }
  for (let i = 0; i < n; i += 1) if (!Number.isFinite(x[i]!)) return null;
  return x;
}

export interface RankResult {
  rank: number;
  /**
   * For each row that is a combination of earlier rows: that row's index and
   * the indices of the rows it depends on (itself included).
   */
  dependent: { row: number; group: number[] }[];
}

/**
 * Rank of a set of row vectors by modified Gram-Schmidt, in row order.
 *
 * A row whose remainder (after removing its projection on the earlier rows)
 * is tiny next to its own length is dependent. For each such row the
 * combination of earlier rows that reproduces it is tracked, which is how
 * the solver names the constraints that clash or repeat one another.
 */
export function rowRank(rows: Float64Array[], tol = 1e-9): RankResult {
  const m = rows.length;
  const basis: Float64Array[] = [];
  /** For each basis vector: its coefficients over the original rows. */
  const coeffs: Float64Array[] = [];
  const dependent: RankResult['dependent'] = [];
  for (let i = 0; i < m; i += 1) {
    const row = rows[i]!;
    const n = row.length;
    let norm0 = 0;
    for (let k = 0; k < n; k += 1) norm0 += row[k]! * row[k]!;
    norm0 = Math.sqrt(norm0);
    const v = Float64Array.from(row);
    const c = new Float64Array(m);
    c[i] = 1;
    // Two passes keep the projection numerically clean.
    for (let pass = 0; pass < 2; pass += 1) {
      for (let j = 0; j < basis.length; j += 1) {
        const q = basis[j]!;
        let dot = 0;
        for (let k = 0; k < n; k += 1) dot += v[k]! * q[k]!;
        if (dot === 0) continue;
        for (let k = 0; k < n; k += 1) v[k] = v[k]! - dot * q[k]!;
        const cj = coeffs[j]!;
        for (let k = 0; k < m; k += 1) c[k] = c[k]! - dot * cj[k]!;
      }
    }
    let norm = 0;
    for (let k = 0; k < n; k += 1) norm += v[k]! * v[k]!;
    norm = Math.sqrt(norm);
    if (norm0 === 0) {
      dependent.push({ row: i, group: [i] });
      continue;
    }
    if (norm <= tol * Math.max(1, norm0)) {
      let cmax = 0;
      for (let k = 0; k < m; k += 1) cmax = Math.max(cmax, Math.abs(c[k]!));
      const group: number[] = [];
      for (let k = 0; k < m; k += 1) if (Math.abs(c[k]!) > 1e-6 * cmax) group.push(k);
      dependent.push({ row: i, group });
      continue;
    }
    for (let k = 0; k < n; k += 1) v[k] = v[k]! / norm;
    for (let k = 0; k < m; k += 1) c[k] = c[k]! / norm;
    basis.push(v);
    coeffs.push(c);
  }
  return { rank: basis.length, dependent };
}

export interface LmResult {
  x: Float64Array;
  /** Half the sum of squared residuals at `x`. */
  cost: number;
  iterations: number;
  converged: boolean;
}

export interface LmOptions {
  maxIterations?: number;
  /** Stop when every residual is below this. */
  residualTol?: number;
  /** Stop when a step moves no parameter more than this. */
  stepTol?: number;
  /** Finite-difference step, relative to max(1, |x|). */
  fdStep?: number;
}

/** Forward-difference Jacobian, one row per residual. */
export function jacobian(
  f: (x: Float64Array) => Float64Array,
  x: Float64Array,
  r0: Float64Array,
  fdStep = 1e-7,
): Float64Array[] {
  const n = x.length;
  const m = r0.length;
  const J: Float64Array[] = Array.from({ length: m }, () => new Float64Array(n));
  const xp = Float64Array.from(x);
  for (let j = 0; j < n; j += 1) {
    const h = fdStep * Math.max(1, Math.abs(x[j]!));
    xp[j] = x[j]! + h;
    const r = f(xp);
    for (let i = 0; i < m; i += 1) J[i]![j] = (r[i]! - r0[i]!) / h;
    xp[j] = x[j]!;
  }
  return J;
}

function halfSq(r: Float64Array): number {
  let s = 0;
  for (let i = 0; i < r.length; i += 1) s += r[i]! * r[i]!;
  return 0.5 * s;
}

function maxAbs(r: Float64Array): number {
  let s = 0;
  for (let i = 0; i < r.length; i += 1) s = Math.max(s, Math.abs(r[i]!));
  return s;
}

/**
 * Levenberg-Marquardt least squares: minimise the sum of squared residuals.
 *
 * The damping adds a multiple of the identity as well as of diag(JᵀJ), so a
 * parameter no residual touches does not move. For an under-constrained
 * sketch that keeps the solution close to what was drawn.
 */
export function levenbergMarquardt(
  f: (x: Float64Array) => Float64Array,
  x0: Float64Array,
  opts: LmOptions = {},
): LmResult {
  const maxIt = opts.maxIterations ?? 100;
  const rTol = opts.residualTol ?? 1e-10;
  const sTol = opts.stepTol ?? 1e-13;
  const n = x0.length;
  let x = Float64Array.from(x0);
  let r = f(x);
  let cost = halfSq(r);
  if (r.length === 0 || maxAbs(r) < rTol) return { x, cost, iterations: 0, converged: true };
  let lambda = 1e-3;
  let it = 0;
  const A = new Float64Array(n * n);
  const g = new Float64Array(n);
  for (; it < maxIt; it += 1) {
    const J = jacobian(f, x, r, opts.fdStep);
    A.fill(0);
    g.fill(0);
    for (let i = 0; i < J.length; i += 1) {
      const row = J[i]!;
      const ri = r[i]!;
      for (let a = 0; a < n; a += 1) {
        const ja = row[a]!;
        if (ja === 0) continue;
        g[a] = g[a]! - ja * ri;
        for (let b = a; b < n; b += 1) A[a * n + b] = A[a * n + b]! + ja * row[b]!;
      }
    }
    for (let a = 0; a < n; a += 1) for (let b = 0; b < a; b += 1) A[a * n + b] = A[b * n + a]!;
    let improved = false;
    for (let tries = 0; tries < 12; tries += 1) {
      const D = Float64Array.from(A);
      for (let a = 0; a < n; a += 1) D[a * n + a] = A[a * n + a]! * (1 + lambda) + lambda * 1e-6;
      const step = solveLinear(D, g, n);
      if (!step) {
        lambda *= 10;
        continue;
      }
      const xn = new Float64Array(n);
      let stepMax = 0;
      for (let a = 0; a < n; a += 1) {
        xn[a] = x[a]! + step[a]!;
        stepMax = Math.max(stepMax, Math.abs(step[a]!));
      }
      const rn = f(xn);
      const cn = halfSq(rn);
      if (Number.isFinite(cn) && cn < cost) {
        x = xn;
        r = rn;
        cost = cn;
        lambda = Math.max(lambda / 3, 1e-12);
        improved = true;
        if (maxAbs(r) < rTol) return { x, cost, iterations: it + 1, converged: true };
        if (stepMax < sTol) return { x, cost, iterations: it + 1, converged: maxAbs(r) < rTol };
        break;
      }
      lambda *= 4;
    }
    if (!improved) break;
  }
  return { x, cost, iterations: it, converged: maxAbs(r) < rTol };
}
