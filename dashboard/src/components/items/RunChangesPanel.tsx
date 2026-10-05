import { Link } from 'react-router-dom';
import { useRunChanges } from '../../hooks/use-items';
import { StatusBadge } from '../shared/StatusBadge';
import { CELL_BORDER, LABEL_STYLE, PANEL_STYLE } from './format';

/** FORGE-526: "This run changed": the revisions the run produced and what
 * each gate did with them (approved into a baseline, still a draft, or
 * closed). */
export function RunChangesPanel({ runId, projectId }: { runId: string; projectId?: string }) {
  const { data, isLoading, isError } = useRunChanges(runId, projectId);

  return (
    <section data-testid="run-changes" className="rounded p-3 mb-4" style={PANEL_STYLE}>
      <div className="font-mono mb-2" style={LABEL_STYLE}>This run changed</div>
      {isLoading && <div className="font-mono text-xs text-on-surface-variant">Loading...</div>}
      {isError && <div role="alert" className="font-mono text-xs text-error">Could not load this run's changes.</div>}
      {data && data.revisions.length === 0 && (
        <div className="font-mono text-xs text-on-surface-variant">No item revisions from this run yet.</div>
      )}
      {data && data.revisions.length > 0 && (
        <table className="w-full text-left border-collapse">
          <tbody>
            {data.revisions.map((r) => (
              <tr key={r.ref} style={{ borderBottom: CELL_BORDER }}>
                <td className="py-1 pr-2">
                  <Link to={`/twin?node=${r.node_id}`} className="font-mono text-xs text-on-surface hover:underline">{r.ref}</Link>
                </td>
                <td className="py-1 pr-2 text-xs text-on-surface-variant">{r.name}</td>
                <td className="py-1 pr-2"><StatusBadge status={r.status} /></td>
                <td className="py-1 pr-2 font-mono" style={{ fontSize: 10, color: 'var(--mf-c-ffb783)' }}>
                  {r.baselined_by_gate ? `gate ${r.baselined_by_gate}` : r.status === 'draft' ? 'waiting for gate' : ''}
                </td>
                <td className="py-1 font-mono text-right" style={{ fontSize: 10, color: r.is_current ? 'var(--mf-c-3dd68c)' : 'var(--mf-c-9a9aaa)' }}>
                  {r.is_current ? 'current' : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {data && data.baselines.length > 0 && (
        <div className="font-mono mt-2" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }}>
          Baselines recorded by this run: {data.baselines.map((b) => `${b.gate_id ?? b.name} (${b.item_count} items)`).join(', ')}
        </div>
      )}
    </section>
  );
}
