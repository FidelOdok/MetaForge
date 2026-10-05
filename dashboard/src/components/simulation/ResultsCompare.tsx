import type { SimulationResult } from '../../types/simulationResult';

interface ResultsCompareProps {
  /** Earlier of the two selected results. */
  a: SimulationResult;
  /** Later of the two selected results. */
  b: SimulationResult;
}

function pctChange(from: number | null, to: number | null): string | null {
  if (from === null || to === null || from === 0) return null;
  const pct = ((to - from) / Math.abs(from)) * 100;
  const sign = pct > 0 ? '+' : '';
  return `${sign}${pct.toFixed(1)}%`;
}

function DeltaCell({
  label,
  unit,
  from,
  to,
  precision,
}: {
  label: string;
  unit: string;
  from: number | null;
  to: number | null;
  precision: number;
}) {
  const change = pctChange(from, to);
  // Rising stress/displacement is worth flagging, not necessarily "bad" —
  // this is a neutral direction indicator, not a pass/fail judgement.
  const rising = from !== null && to !== null && to > from;
  const falling = from !== null && to !== null && to < from;

  return (
    <div className="flex flex-col gap-1">
      <span className="font-mono text-[10px] uppercase tracking-widest text-on-surface-variant">
        {label}
      </span>
      <div className="flex items-baseline gap-2">
        <span className="font-mono text-xs text-on-surface-variant">
          {from === null ? '—' : `${from.toFixed(precision)} ${unit}`}
        </span>
        <span className="text-on-surface-variant">→</span>
        <span className="font-mono text-xs text-on-surface">
          {to === null ? '—' : `${to.toFixed(precision)} ${unit}`}
        </span>
        {change && (
          <span
            className="font-mono text-[10px]"
            style={{ color: rising ? '#ff5a0a' : falling ? '#3b82f6' : undefined }}
          >
            {change}
          </span>
        )}
      </div>
    </div>
  );
}

/** FORGE-279: numeric side-by-side comparison of two simulation_result work
 * products. FORGE-532 added the 3D contour compare beside it
 * (FieldCompare), for results that stored their field. */
export function ResultsCompare({ a, b }: ResultsCompareProps) {
  return (
    <div
      data-testid="results-compare"
      className="mt-3 rounded-lg p-4"
      style={{ background: 'rgba(30,31,38,0.85)', border: '1px solid rgba(65,72,90,0.2)' }}
    >
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-mono text-xs text-on-surface" style={{ margin: 0 }}>
          {a.name} → {b.name}
        </h3>
      </div>
      <div className="grid grid-cols-2 gap-6">
        <DeltaCell label="Max stress" unit="MPa" from={a.maxVonMisesMpa} to={b.maxVonMisesMpa} precision={1} />
        <DeltaCell
          label="Max displacement"
          unit="mm"
          from={a.maxDisplacementMm}
          to={b.maxDisplacementMm}
          precision={3}
        />
      </div>
    </div>
  );
}
