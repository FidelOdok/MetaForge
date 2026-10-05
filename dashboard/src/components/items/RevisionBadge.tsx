import { useState } from 'react';
import type { RevisionIndexEntry } from '../../api/endpoints/items';
import { ItemHistoryPanel } from './ItemHistoryPanel';

/** FORGE-526: a row's revision (`KEY@n`) with a link to the item's history,
 * for tables whose rows are revisions (BOM components, requirements). An
 * older revision is marked so a stale row stands out. */
export function RevisionBadge({
  entry,
  projectId,
}: {
  entry: RevisionIndexEntry | undefined;
  projectId?: string;
}) {
  const [open, setOpen] = useState(false);
  if (!entry) return null;
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title={`${entry.ref}: open history (${entry.revision_count} revision${entry.revision_count === 1 ? '' : 's'})`}
        data-testid="revision-badge"
        className="font-mono rounded hover:underline"
        style={{
          fontSize: 10,
          padding: '1px 5px',
          background: 'var(--mf-c-282a30)',
          color: entry.current ? 'var(--mf-c-9a9aaa)' : 'var(--mf-c-f59e0b)',
          border: 'none',
          cursor: 'pointer',
        }}
      >
        @{entry.revision}
        {!entry.current && ' (old)'}
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={`${entry.key} history`}
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto"
          style={{ background: 'rgba(0,0,0,0.5)', paddingTop: 48 }}
          onClick={(e) => {
            if (e.target === e.currentTarget) setOpen(false);
          }}
        >
          <div className="w-full mx-4" style={{ maxWidth: 720 }}>
            <ItemHistoryPanel itemKey={entry.key} projectId={projectId} onClose={() => setOpen(false)} />
          </div>
        </div>
      )}
    </>
  );
}
