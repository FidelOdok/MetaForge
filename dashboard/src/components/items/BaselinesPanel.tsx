import { useEffect, useState } from 'react';
import { useBaselineDiff, useBaselines } from '../../hooks/use-items';
import { Badge } from '../ui/Badge';
import { formatRelativeTime } from '../../utils/format-time';
import { CELL_BORDER, LABEL_STYLE, PANEL_STYLE, shortId } from './format';

const STATUS_VARIANT = { unchanged: 'default', changed: 'warning', added: 'success', removed: 'error' } as const;

/** FORGE-526: a project's baselines (one per gate approval, plus any made
 * with twin.create_baseline) and an item-by-item diff of two of them. */
export function BaselinesPanel({ projectId }: { projectId: string }) {
  const { data: baselines, isLoading, isError } = useBaselines(projectId);
  const [a, setA] = useState('');
  const [b, setB] = useState('current');

  useEffect(() => {
    const newest = baselines?.[0];
    if (newest && !a) setA(newest.id);
  }, [baselines, a]);

  const { data: diff, isFetching } = useBaselineDiff(a || undefined, b || undefined);
  const changed = diff ? diff.items.filter((i) => i.status !== 'unchanged') : [];

  return (
    <div data-testid="baselines-panel" className="glass rounded overflow-hidden" style={PANEL_STYLE}>
      <div className="flex items-center justify-between px-4 py-2" style={{ borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}>
        <span className="font-mono" style={{ ...LABEL_STYLE, letterSpacing: '0.1em' }}>Baselines</span>
        <span className="font-mono" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }}>{baselines?.length ?? 0} recorded</span>
      </div>
      <div className="px-4 py-3">
        {isLoading && <div className="font-mono text-xs text-on-surface-variant">Loading baselines...</div>}
        {isError && <div role="alert" className="font-mono text-xs text-error">Could not load baselines.</div>}
        {baselines && baselines.length === 0 && (
          <div className="font-mono text-xs text-on-surface-variant">
            No baselines yet. A gate approval records one automatically.
          </div>
        )}
        {baselines && baselines.length > 0 && (
          <>
            <table className="w-full text-left border-collapse mb-3">
              <tbody>
                {baselines.map((bl) => (
                  <tr key={bl.id} style={{ borderBottom: CELL_BORDER }}>
                    <td className="py-1 pr-2 text-xs text-on-surface">{bl.name}</td>
                    <td className="py-1 pr-2 font-mono" style={{ fontSize: 10, color: 'var(--mf-c-ffb783)' }}>{bl.gate_id ?? bl.source}</td>
                    <td className="py-1 pr-2 font-mono" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }}>{bl.run_id ? `run ${shortId(bl.run_id)}` : ''}</td>
                    <td className="py-1 pr-2 font-mono" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }}>{bl.approved_by.join(', ')}</td>
                    <td className="py-1 pr-2 font-mono" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }}>{bl.item_count} items</td>
                    <td className="py-1 font-mono text-right" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }}>{formatRelativeTime(bl.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="flex items-center gap-2 flex-wrap mb-2">
              <span className="font-mono" style={LABEL_STYLE}>Compare</span>
              <select aria-label="Baseline A" value={a} onChange={(e) => setA(e.target.value)} className="rounded px-2 py-1 text-xs" style={{ background: 'var(--mf-c-1e1f26)', color: 'var(--mf-c-e2e2eb)', border: '1px solid var(--mf-r-65-72-90-0p3)' }}>
                {baselines.map((bl) => (
                  <option key={bl.id} value={bl.id}>{bl.name}</option>
                ))}
              </select>
              <span className="text-xs text-on-surface-variant">with</span>
              <select aria-label="Baseline B" value={b} onChange={(e) => setB(e.target.value)} className="rounded px-2 py-1 text-xs" style={{ background: 'var(--mf-c-1e1f26)', color: 'var(--mf-c-e2e2eb)', border: '1px solid var(--mf-r-65-72-90-0p3)' }}>
                <option value="current">Current items</option>
                {baselines.filter((bl) => bl.id !== a).map((bl) => (
                  <option key={bl.id} value={bl.id}>{bl.name}</option>
                ))}
              </select>
            </div>
            {isFetching && !diff && <div className="font-mono text-xs text-on-surface-variant">Comparing...</div>}
            {diff && (
              <div data-testid="baseline-diff">
                <div className="flex gap-2 mb-2">
                  {(['changed', 'added', 'removed', 'unchanged'] as const).map((s) => (
                    <Badge key={s} variant={STATUS_VARIANT[s]}>{diff.counts[s]} {s}</Badge>
                  ))}
                </div>
                {changed.length === 0 ? (
                  <div className="font-mono text-xs text-on-surface-variant">No item changed.</div>
                ) : (
                  <table className="w-full text-left border-collapse">
                    <tbody>
                      {changed.map((i) => (
                        <tr key={i.key} style={{ borderBottom: CELL_BORDER }}>
                          <td className="py-1 pr-2 font-mono text-xs text-on-surface">{i.key}</td>
                          <td className="py-1 pr-2"><Badge variant={STATUS_VARIANT[i.status]}>{i.status}</Badge></td>
                          <td className="py-1 font-mono text-xs text-on-surface-variant">
                            {i.from_ref ?? ''}{i.from_ref && i.to_ref ? ' to ' : ''}{i.to_ref ?? ''}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
