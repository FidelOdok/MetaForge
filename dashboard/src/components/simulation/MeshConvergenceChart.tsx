import { useState } from 'react';
import type { MeshConvergence, MeshConvergencePoint } from '../../types/simulationField';

const INK = 'var(--mf-c-e2e2eb)';
const MUTED = 'var(--mf-c-9a9aaa)';
const GRID = 'rgba(65,72,90,0.35)';
const SERIES = '#86cfff'; // Kinetic Console tertiary
const SUCCESS = '#3dd68c';
const WARNING = '#f59e0b';

const W = 420;
const H = 200;
const PAD = { left: 52, right: 16, top: 12, bottom: 34 };

export interface ConvergencePlotPoint {
  x: number;
  y: number;
  point: MeshConvergencePoint;
}

/** Plot points for a convergence sweep: x is the element count when every
 * point has one (the chart the ticket asks for), else element size, plotted
 * coarse to fine either way. */
export function convergencePlotPoints(points: MeshConvergencePoint[]): {
  axis: 'element_count' | 'element_size_mm';
  points: ConvergencePlotPoint[];
} {
  const byCount = points.length > 0 && points.every((p) => typeof p.element_count === 'number');
  const axis = byCount ? 'element_count' : 'element_size_mm';
  const sorted = [...points].sort((p, q) =>
    byCount ? (p.element_count as number) - (q.element_count as number) : q.element_size_mm - p.element_size_mm,
  );
  return {
    axis,
    points: sorted.map((p) => ({
      x: byCount ? (p.element_count as number) : p.element_size_mm,
      y: p.max_von_mises_mpa,
      point: p,
    })),
  };
}

function ticks(min: number, max: number, count = 4): number[] {
  if (min === max) return [min];
  const step = (max - min) / count;
  return Array.from({ length: count + 1 }, (_, i) => min + step * i);
}

function fmt(v: number): string {
  return Number(v.toPrecision(3)).toLocaleString();
}

/**
 * FORGE-532: a calculix.check_mesh_convergence sweep as a chart, peak von
 * Mises against mesh refinement, with the converged / not converged verdict
 * (icon plus words, never colour alone) and its recommendation.
 */
export function MeshConvergenceChart({ convergence }: { convergence: MeshConvergence }) {
  const { axis, points } = convergencePlotPoints(convergence.points ?? []);
  const [hover, setHover] = useState<number | null>(null);
  if (points.length < 2) return null;

  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  const yLo = Math.min(...ys);
  const yHi = Math.max(...ys);
  const yPad = (yHi - yLo) * 0.15 || Math.abs(yHi) * 0.1 || 1;
  const yMin = yLo - yPad;
  const yMax = yHi + yPad;
  const sx = (x: number) =>
    PAD.left + (xMax === xMin ? 0.5 : axis === 'element_size_mm' ? (xMax - x) / (xMax - xMin) : (x - xMin) / (xMax - xMin)) * (W - PAD.left - PAD.right);
  const sy = (y: number) => PAD.top + (1 - (y - yMin) / (yMax - yMin)) * (H - PAD.top - PAD.bottom);
  const path = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(' ');
  const hovered = hover !== null ? points[hover] : null;
  const lastChange = convergence.changes?.[convergence.changes.length - 1]?.percent_change;

  return (
    <div
      data-testid="mesh-convergence-chart"
      className="rounded-lg p-4"
      style={{ background: 'rgba(30,31,38,0.85)', border: '1px solid rgba(65,72,90,0.2)' }}
    >
      <div className="mb-2 flex items-center justify-between gap-3">
        <h3 className="font-mono text-xs" style={{ margin: 0, color: INK }}>
          Mesh convergence: peak von Mises vs {axis === 'element_count' ? 'element count' : 'element size'}
        </h3>
        <span
          data-testid="convergence-verdict"
          className="flex items-center gap-1 rounded px-2 py-0.5 font-mono"
          style={{
            fontSize: 11,
            color: convergence.converged ? SUCCESS : WARNING,
            border: `1px solid ${convergence.converged ? 'rgba(61,214,140,0.4)' : 'rgba(245,158,11,0.4)'}`,
          }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 14 }}>
            {convergence.converged ? 'check_circle' : 'warning'}
          </span>
          {convergence.converged ? 'Converged' : 'Not converged'}
          {typeof lastChange === 'number' ? ` (${lastChange}% last step)` : ''}
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Peak von Mises stress across ${points.length} mesh refinements`}
        style={{ width: '100%', maxWidth: W, height: 'auto' }}
        onMouseLeave={() => setHover(null)}
      >
        {ticks(yMin, yMax).map((t) => (
          <g key={`y${t}`}>
            <line x1={PAD.left} x2={W - PAD.right} y1={sy(t)} y2={sy(t)} stroke={GRID} strokeWidth={1} />
            <text x={PAD.left - 6} y={sy(t) + 3} textAnchor="end" fontSize={9} fill={MUTED} fontFamily="Roboto Mono, monospace">
              {fmt(t)}
            </text>
          </g>
        ))}
        {points.map((p, i) => (
          <text
            key={`x${i}`}
            x={sx(p.x)}
            y={H - PAD.bottom + 14}
            textAnchor="middle"
            fontSize={9}
            fill={MUTED}
            fontFamily="Roboto Mono, monospace"
          >
            {axis === 'element_count' ? fmt(p.x) : `${fmt(p.x)} mm`}
          </text>
        ))}
        <text x={W / 2} y={H - 4} textAnchor="middle" fontSize={9} fill={MUTED}>
          {axis === 'element_count' ? 'Elements (coarse to fine)' : 'Element size (coarse to fine)'}
        </text>
        <text x={10} y={PAD.top + 4} fontSize={9} fill={MUTED}>
          MPa
        </text>
        <path d={path} fill="none" stroke={SERIES} strokeWidth={2} />
        {points.map((p, i) => (
          <g key={`p${i}`} onMouseEnter={() => setHover(i)}>
            {/* Hit target bigger than the mark. */}
            <circle cx={sx(p.x)} cy={sy(p.y)} r={12} fill="transparent" />
            <circle
              data-testid="convergence-point"
              cx={sx(p.x)}
              cy={sy(p.y)}
              r={4}
              fill={SERIES}
              stroke="#1e1f26"
              strokeWidth={2}
            />
          </g>
        ))}
        {hovered && (
          <g pointerEvents="none">
            <rect
              x={Math.min(sx(hovered.x) + 8, W - 150)}
              y={Math.max(sy(hovered.y) - 34, 2)}
              width={140}
              height={30}
              rx={4}
              fill="#1e1f26"
              stroke={GRID}
            />
            <text
              x={Math.min(sx(hovered.x) + 14, W - 144)}
              y={Math.max(sy(hovered.y) - 21, 15)}
              fontSize={9}
              fill={INK}
              fontFamily="Roboto Mono, monospace"
            >
              {fmt(hovered.y)} MPa
            </text>
            <text
              x={Math.min(sx(hovered.x) + 14, W - 144)}
              y={Math.max(sy(hovered.y) - 9, 27)}
              fontSize={9}
              fill={MUTED}
              fontFamily="Roboto Mono, monospace"
            >
              {hovered.point.element_size_mm} mm
              {hovered.point.element_count !== undefined ? `, ${fmt(hovered.point.element_count)} el` : ''}
            </text>
          </g>
        )}
      </svg>
      {convergence.recommendation && (
        <p className="mt-2" style={{ margin: 0, fontSize: 11, color: MUTED }}>
          {convergence.recommendation}
        </p>
      )}
      {/* Table view of the same numbers, for screen readers and exact reading. */}
      <table className="sr-only">
        <caption>Mesh convergence points</caption>
        <thead>
          <tr>
            <th>Element size (mm)</th>
            <th>Elements</th>
            <th>Peak von Mises (MPa)</th>
          </tr>
        </thead>
        <tbody>
          {points.map((p, i) => (
            <tr key={i}>
              <td>{p.point.element_size_mm}</td>
              <td>{p.point.element_count ?? ''}</td>
              <td>{p.point.max_von_mises_mpa}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
