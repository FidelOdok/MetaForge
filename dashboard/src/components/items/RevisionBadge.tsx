import { useState } from 'react';
import type { RevisionIndexEntry } from '../../api/endpoints/items';
import { ItemHistoryPanel } from './ItemHistoryPanel';
import { Modal } from '../ui/Modal';

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
      <Modal open={open} onClose={() => setOpen(false)} title={`${entry.key} history`} size="lg">
        <ItemHistoryPanel itemKey={entry.key} projectId={projectId} />
      </Modal>
    </>
  );
}
