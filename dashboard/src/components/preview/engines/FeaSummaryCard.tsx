import { Link } from 'react-router-dom';
import { PC } from '../tokens';
import type { TwinNode } from '../../../types/twin';

/**
 * FORGE-531: the `sim` preview engine (moved from TwinViewerPage, where it
 * was FeaResultSection). Same card in the inspector, modal and project rows.
 *
 * FORGE-305: what an FEA result actually said, for a selected
 * simulation_result node.
 *
 * FORGE-246 landed the data-model half -- a real simulation_result work
 * product with max von Mises, max displacement, load case and mesh stats,
 * instead of an evidence entity with the numbers restated as prose -- and
 * FORGE-279 added the read route the Sim page lists from. The inspector
 * never got the other half: selecting a result in the Twin Viewer showed the
 * generic scalar Properties table, so the numbers appeared as
 * `max_von_mises_mpa: 182.4` with no units, no ordering and no indication of
 * which of the forty-odd rows were the answer.
 *
 * Deliberately *not* the Twin Viewer's "Sim" tab. That tab is an
 * already-shipped robotics-physics preview (gravity, joint constraints, a
 * Run/Stop toggle gated on a robot_description node) and shares nothing with
 * an FEA result but the word "sim".
 *
 * Also deliberately not a contour overlay. Per-element stress mapping needs
 * the raw .frd data, which FORGE-246 does not persist anywhere -- only the
 * summary JSON. A contour view is a larger follow-up on top of this, and
 * faking one from the summary would be inventing a field that was never
 * computed.
 */
export function FeaSummaryCard({ node }: { node: TwinNode }) {
  if (node.properties.wp_type !== 'simulation_result') return null;

  const vonMises = numericProperty(node, 'max_von_mises_mpa');
  const displacement = numericProperty(node, 'max_displacement_mm');
  const loadCase = node.properties.load_case;
  const meshStats = node.meshStats;

  // A result node with none of these is a result in name only. Say so rather
  // than render an empty table -- a blank panel reads as "the view is broken",
  // which is the wrong conclusion.
  const hasSummary = vonMises !== null || displacement !== null;

  return (
    <div className="px-3 py-2 flex-shrink-0" data-testid="fea-result-section" style={{ borderBottom: `1px solid ${PC.border}` }}>
      <div className="flex items-center justify-between mb-1.5">
        <div className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.1em', color: PC.onSurfaceVariant }}>
          FEA result
        </div>
        {/* The Sim page is where two results compare side by side (FORGE-279);
            this panel is one result, so it points there rather than
            duplicating the comparison. */}
        <Link
          to="/sim"
          style={{ fontSize: 10, color: PC.onSurfaceVariant, textDecoration: 'none' }}
        >
          Compare in Sim &rarr;
        </Link>
      </div>

      {!hasSummary && (
        <div style={{ fontSize: 11, color: PC.onSurfaceVariant }}>
          No stress or displacement was recorded for this result. The work
          product exists, but the numbers never reached it.
        </div>
      )}

      {hasSummary && (
        <div className="grid grid-cols-2 gap-2">
          <FeaMetric label="Max von Mises" value={vonMises} unit="MPa" precision={1} />
          <FeaMetric label="Max displacement" value={displacement} unit="mm" precision={3} />
        </div>
      )}

      <div className="mt-2 font-mono" style={{ fontSize: 10, color: PC.onSurfaceVariant }}>
        {/* Free-text, not a load-case node id -- the backend is explicit about
            that, so this does not pretend to be a link. */}
        Load case · {typeof loadCase === 'string' && loadCase ? loadCase : 'not recorded'}
      </div>

      {meshStats && Object.keys(meshStats).length > 0 && (
        <div className="mt-2 pt-2" style={{ borderTop: `1px solid ${PC.border}` }}>
          <div className="font-mono uppercase mb-1" style={{ fontSize: 10, letterSpacing: '0.1em', color: PC.onSurfaceVariant }}>
            Mesh
          </div>
          <table className="w-full" style={{ borderCollapse: 'collapse' }}>
            <tbody>
              {Object.entries(meshStats).map(([k, v]) => (
                <tr key={k}>
                  <td className="py-0.5 pr-3 font-mono" style={{ fontSize: 11, color: PC.onSurfaceVariant, width: '55%' }}>
                    {k}
                  </td>
                  <td className="py-0.5 font-mono" style={{ fontSize: 11, color: PC.onSurface }}>
                    {formatMeshStat(v)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** A property that should be a number, or null -- never NaN. The twin's
 *  scalar projection can hand back a string for a value an agent wrote as
 *  one, and `Number('')` is 0, which would read as a real measurement of
 *  zero stress. */
function numericProperty(node: TwinNode, key: string): number | null {
  const raw = node.properties[key];
  if (typeof raw === 'number') return Number.isFinite(raw) ? raw : null;
  if (typeof raw === 'string' && raw.trim() !== '') {
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/** Mesh stats are counts and ratios written by whichever tool produced the
 *  mesh, so the shape is not fixed. Render what is there without asserting a
 *  schema over it. */
function formatMeshStat(value: unknown): string {
  if (typeof value === 'number') return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(3);
  if (typeof value === 'boolean' || typeof value === 'string') return String(value);
  if (value === null || value === undefined) return '-';
  return JSON.stringify(value);
}

function FeaMetric({
  label,
  value,
  unit,
  precision,
}: {
  label: string;
  value: number | null;
  unit: string;
  precision: number;
}) {
  return (
    <div className="rounded px-2 py-1.5" style={{ background: PC.surfaceHigh }}>
      <div className="font-mono uppercase" style={{ fontSize: 9, letterSpacing: '0.1em', color: PC.onSurfaceVariant }}>
        {label}
      </div>
      <div className="font-mono" style={{ fontSize: 13, color: PC.onSurface }}>
        {value === null ? '-' : `${value.toFixed(precision)} `}
        {value !== null && <span style={{ fontSize: 10, color: PC.onSurfaceVariant }}>{unit}</span>}
      </div>
    </div>
  );
}
