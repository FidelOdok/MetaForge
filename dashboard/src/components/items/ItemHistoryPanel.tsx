import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useItemHistory } from '../../hooks/use-items';
import { StatusBadge } from '../shared/StatusBadge';
import { formatRelativeTime } from '../../utils/format-time';
import { RevisionCompare } from './RevisionCompare';
import { LABEL_STYLE, shortId } from './format';

/** FORGE-526: one item's revisions as a timeline (rev, status, run, gate,
 * reason), and a Compare view between any two of them. Opt-in: opened from a
 * row's "N revisions" link, never shown by default. */
export function ItemHistoryPanel({
  itemKey,
  projectId,
  onClose,
}: {
  itemKey: string;
  projectId?: string;
  onClose?: () => void;
}) {
  const { data: history, isLoading, isError } = useItemHistory(itemKey, projectId);
  const [picked, setPicked] = useState<number[]>([]);

  // Default compare: the revision before the current one, and the current one.
  useEffect(() => {
    if (!history) return;
    const revs = history.revisions.map((r) => r.revision);
    const cur = history.current?.revision ?? revs[revs.length - 1];
    const prev = revs.filter((r) => r < (cur ?? 0)).pop();
    setPicked(prev !== undefined && cur !== undefined ? [prev, cur] : []);
  }, [history]);

  const toggle = (rev: number) =>
    setPicked((p) => (p.includes(rev) ? p.filter((r) => r !== rev) : [...p, rev].slice(-2)));

  const [a, b] = [...picked].sort((x, y) => x - y);

  return (
    <div data-testid="item-history" className="rounded p-3" style={{ background: 'var(--mf-c-111319)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}>
      <div className="flex items-center justify-between mb-2">
        <span className="font-mono" style={LABEL_STYLE}>History · {itemKey}</span>
        {onClose && (
          <button type="button" onClick={onClose} aria-label="Close history" className="rounded hover:bg-surface-high" style={{ color: 'var(--mf-c-9a9aaa)', padding: 2 }}>
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>close</span>
          </button>
        )}
      </div>
      {isLoading && <div className="font-mono text-xs text-on-surface-variant">Loading revisions...</div>}
      {isError && <div role="alert" className="font-mono text-xs text-error">Could not load this item's history.</div>}
      {history && (
        <>
          <ol className="relative" style={{ borderLeft: '1px solid var(--mf-r-65-72-90-0p3)', marginLeft: 6 }}>
            {[...history.revisions].reverse().map((r) => {
              const isCurrent = history.current?.revision === r.revision;
              return (
                <li key={r.revision} className="pl-4 py-1.5 relative" data-testid={`revision-${r.revision}`}>
                  <span
                    style={{
                      position: 'absolute',
                      left: -5,
                      top: 12,
                      width: 9,
                      height: 9,
                      borderRadius: '50%',
                      background: isCurrent ? 'var(--mf-c-3dd68c)' : r.status === 'draft' ? 'var(--mf-c-f59e0b)' : 'var(--mf-c-33343b)',
                      border: '1px solid var(--mf-r-65-72-90-0p3)',
                    }}
                  />
                  <div className="flex items-center gap-2 flex-wrap">
                    <label className="flex items-center gap-1 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={picked.includes(r.revision)}
                        onChange={() => toggle(r.revision)}
                        aria-label={`Compare ${itemKey}@${r.revision}`}
                      />
                      <Link to={`/twin?node=${r.node_id}`} className="font-mono text-xs text-on-surface hover:underline">
                        @{r.revision}
                      </Link>
                    </label>
                    <StatusBadge status={r.status} />
                    {isCurrent && <span className="font-mono" style={{ fontSize: 10, color: 'var(--mf-c-3dd68c)' }}>CURRENT</span>}
                    {r.run_id && (
                      <Link to={`/runs/${r.run_id}`} className="font-mono hover:underline" style={{ fontSize: 10, color: 'var(--mf-c-86cfff)' }}>
                        run {shortId(r.run_id)}
                      </Link>
                    )}
                    {r.gate && <span className="font-mono" style={{ fontSize: 10, color: 'var(--mf-c-ffb783)' }}>gate {r.gate}</span>}
                    {r.baselines.length > 0 && (
                      <span className="font-mono" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }}>
                        in {r.baselines.length} baseline{r.baselines.length === 1 ? '' : 's'}
                      </span>
                    )}
                    {r.created_at && (
                      <span className="font-mono ml-auto" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }}>{formatRelativeTime(r.created_at)}</span>
                    )}
                  </div>
                  {(r.change_reason || r.status_reason) && (
                    <div className="text-xs text-on-surface-variant mt-0.5" style={{ lineHeight: 1.4 }}>
                      {r.change_reason || r.status_reason}
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
          {a && b ? (
            <RevisionCompare itemKey={history.item.key} itemType={history.item.item_type} a={a} b={b} projectId={projectId} />
          ) : (
            history.revisions.length > 1 && (
              <div className="font-mono text-xs text-on-surface-variant mt-2">Tick two revisions to compare them.</div>
            )
          )}
        </>
      )}
    </div>
  );
}
