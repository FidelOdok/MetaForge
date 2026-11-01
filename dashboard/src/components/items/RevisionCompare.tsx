import { lazy, Suspense } from 'react';
import { Link } from 'react-router-dom';
import { useItemDiff } from '../../hooks/use-items';
import type { FieldChange } from '../../api/endpoints/items';
import { Badge } from '../ui/Badge';
import { CELL_BORDER, GEOMETRY_TYPES, LABEL_STYLE, fmtDelta, fmtNum, fmtValue } from './format';

// three.js loads only when a geometry compare is actually opened.
const RevisionOverlay = lazy(() =>
  import('./RevisionOverlay').then((m) => ({ default: m.RevisionOverlay })),
);

const CHANGE_VARIANT = { changed: 'warning', added: 'success', removed: 'error' } as const;

function ChangeTable({ title, rows, testId }: { title: string; rows: FieldChange[]; testId: string }) {
  if (rows.length === 0) return null;
  return (
    <div className="mt-3" data-testid={testId}>
      <div className="font-mono mb-1" style={LABEL_STYLE}>{title}</div>
      <table className="w-full text-left border-collapse">
        <tbody>
          {rows.map((r) => (
            <tr key={r.name} style={{ borderBottom: CELL_BORDER }}>
              <td className="py-1 pr-2 font-mono text-xs text-on-surface">{r.name}</td>
              <td className="py-1 pr-2"><Badge variant={CHANGE_VARIANT[r.status]}>{r.status}</Badge></td>
              <td className="py-1 pr-2 font-mono text-xs text-on-surface-variant">{fmtValue(r.from)}</td>
              <td className="py-1 font-mono text-xs text-on-surface">{fmtValue(r.to)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Compare KEY@a with KEY@b: geometry overlay and deltas, parameter and
 * requirement changes, and what is still pinned to the older revision. */
export function RevisionCompare({
  itemKey,
  itemType,
  a,
  b,
  projectId,
}: {
  itemKey: string;
  itemType: string;
  a: number;
  b: number;
  projectId?: string;
}) {
  const { data: diff, isLoading, isError } = useItemDiff(itemKey, a, b, projectId);

  if (isLoading) {
    return <div className="font-mono text-xs text-on-surface-variant py-2">Comparing {itemKey}@{a} and @{b}...</div>;
  }
  if (isError || !diff) {
    return <div role="alert" className="font-mono text-xs text-error py-2">Could not compare these revisions.</div>;
  }

  const geo = diff.geometry;
  const nothing =
    !geo?.available &&
    diff.parameters.length === 0 &&
    diff.requirements.length === 0 &&
    diff.fields.length === 0;

  return (
    <div data-testid="revision-compare" className="mt-2">
      <div className="font-mono text-xs text-on-surface mb-2">
        {diff.a_ref} <span className="text-on-surface-variant">to</span> {diff.b_ref}
      </div>

      {GEOMETRY_TYPES.has(itemType) && (
        <Suspense fallback={<div className="font-mono text-xs text-on-surface-variant">Loading viewer...</div>}>
          <RevisionOverlay
            oldNodeId={diff.a.node_id}
            newNodeId={diff.b.node_id}
            oldLabel={diff.a_ref}
            newLabel={diff.b_ref}
          />
        </Suspense>
      )}

      {geo?.available && (
        <div className="mt-3" data-testid="geometry-deltas">
          <div className="font-mono mb-1" style={LABEL_STYLE}>
            Geometry ({geo.source === 'describe_step_file' ? 'measured from STEP' : 'as recorded'})
          </div>
          <table className="w-full text-left border-collapse">
            <tbody>
              <tr style={{ borderBottom: CELL_BORDER }}>
                <td className="py-1 pr-2 font-mono text-xs text-on-surface-variant">Volume mm3</td>
                <td className="py-1 pr-2 font-mono text-xs">{fmtNum(geo.a.volume_mm3)}</td>
                <td className="py-1 pr-2 font-mono text-xs">{fmtNum(geo.b.volume_mm3)}</td>
                <td className="py-1 font-mono text-xs text-primary">{fmtDelta(geo.volume_delta_mm3)}</td>
              </tr>
              <tr style={{ borderBottom: CELL_BORDER }}>
                <td className="py-1 pr-2 font-mono text-xs text-on-surface-variant">Mass kg</td>
                <td className="py-1 pr-2 font-mono text-xs">{fmtNum(geo.a.mass_kg, 4)}</td>
                <td className="py-1 pr-2 font-mono text-xs">{fmtNum(geo.b.mass_kg, 4)}</td>
                <td className="py-1 font-mono text-xs text-primary">
                  {geo.mass_delta_kg !== null ? fmtDelta(geo.mass_delta_kg, 4) : 'unknown'}
                </td>
              </tr>
              <tr style={{ borderBottom: CELL_BORDER }}>
                <td className="py-1 pr-2 font-mono text-xs text-on-surface-variant">Bounding box</td>
                <td className="py-1 pr-2 font-mono text-xs">{fmtValue(geo.a.bounding_box)}</td>
                <td className="py-1 pr-2 font-mono text-xs">{fmtValue(geo.b.bounding_box)}</td>
                <td className="py-1 font-mono text-xs text-primary">{fmtValue(geo.bounding_box_delta)}</td>
              </tr>
            </tbody>
          </table>
          {geo.mass_source && geo.mass_source !== 'recorded' && (
            <div className="font-mono mt-1" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }}>Mass from {geo.mass_source}</div>
          )}
        </div>
      )}

      <ChangeTable title="Parameters" rows={diff.parameters} testId="parameter-changes" />
      <ChangeTable title="Requirement values" rows={diff.requirements} testId="requirement-changes" />
      <ChangeTable title="Fields" rows={diff.fields} testId="field-changes" />

      {nothing && (
        <div className="font-mono text-xs text-on-surface-variant mt-2">No recorded differences between these revisions.</div>
      )}

      {diff.dependents.length > 0 && (
        <div className="mt-3" data-testid="pinned-dependents">
          <div className="font-mono mb-1" style={LABEL_STYLE}>Still pinned to {diff.a_ref}</div>
          {diff.dependents.map((d) => (
            <div key={d.node_id} className="flex items-center gap-2 py-0.5">
              <Badge variant="warning">{d.type.replace(/_/g, ' ')}</Badge>
              <Link to={`/twin?node=${d.node_id}`} className="text-xs text-on-surface hover:underline">{d.name}</Link>
            </div>
          ))}
        </div>
      )}

      {diff.warnings.map((w) => (
        <div key={w} className="font-mono mt-2" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }}>{w}</div>
      ))}
    </div>
  );
}
