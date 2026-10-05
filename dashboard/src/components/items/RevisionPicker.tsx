import { useItemHistory } from '../../hooks/use-items';

/** FORGE-526: the viewer's revision picker (@n) for a loaded item. Shows
 * only when the node is a revision of an item (it carries `item_key`);
 * picking another revision selects that revision's node. */
export function RevisionPicker({
  itemKey,
  nodeId,
  projectId,
  onSelect,
}: {
  itemKey: string | undefined;
  nodeId: string;
  projectId?: string;
  onSelect: (nodeId: string) => void;
}) {
  const { data: history } = useItemHistory(itemKey, projectId);
  if (!itemKey || !history || history.revisions.length < 2) return null;
  const selected = history.revisions.find((r) => r.node_id === nodeId);
  return (
    <label className="flex items-center gap-1 font-mono" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }}>
      <span>{history.item.key}</span>
      <select
        aria-label="Revision"
        data-testid="revision-picker"
        value={selected?.node_id ?? ''}
        onChange={(e) => e.target.value && onSelect(e.target.value)}
        className="rounded px-1 py-0.5"
        style={{ background: 'var(--mf-c-1e1f26)', color: 'var(--mf-c-e2e2eb)', border: '1px solid var(--mf-r-65-72-90-0p3)', fontSize: 10 }}
      >
        {!selected && <option value="">@?</option>}
        {[...history.revisions].reverse().map((r) => (
          <option key={r.node_id} value={r.node_id}>
            @{r.revision}
            {history.current?.revision === r.revision ? ' current' : r.status !== 'committed' && r.status !== 'approved' ? ` ${r.status}` : ''}
          </option>
        ))}
      </select>
    </label>
  );
}
