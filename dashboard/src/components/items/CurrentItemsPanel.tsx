import { lazy, Suspense, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type { CurrentItemRow, CurrentView, RecordRow } from '../../api/endpoints/items';
import { StatusBadge } from '../shared/StatusBadge';
import { Badge } from '../ui/Badge';
import { EmptyState } from '../ui/EmptyState';
import { formatRelativeTime } from '../../utils/format-time';
import { ItemHistoryPanel } from './ItemHistoryPanel';
import { LABEL_STYLE, PANEL_STYLE, itemTypeLabel, shortId } from './format';

// FORGE-531: the preview registry and engines load only when a row's
// Preview toggle is first opened.
const ProjectRowPreview = lazy(() =>
  import('../preview/ProjectRowPreview').then((m) => ({ default: m.ProjectRowPreview })),
);

const TYPE_ICON: Record<string, string> = {
  cad_model: 'view_in_ar',
  assembly: 'deployed_code',
  constraint_set: 'rule',
  intent: 'flag',
  stakeholder_need: 'groups',
  objective: 'target',
  bom: 'list_alt',
  component_selection: 'memory',
};

const DOT: Record<string, string> = {
  valid: 'var(--mf-c-3dd68c)',
  warning: 'var(--mf-c-f59e0b)',
  error: 'var(--mf-c-ffb4ab)',
};

const EVIDENCE: Record<CurrentItemRow['evidence_state'], { label: string; variant: 'success' | 'warning' | 'default' }> = {
  current: { label: 'evidence current', variant: 'success' },
  out_of_date: { label: 'evidence out of date', variant: 'warning' },
  none: { label: 'no evidence', variant: 'default' },
};

const RECORD_LABEL: Record<RecordRow['record_type'], string> = {
  design_decision: 'Decisions',
  simulation_result: 'Simulation results',
  evidence: 'Evidence',
};

const QUIET_STATUS = new Set(['created', 'unknown']);

/** Gate labels can arrive with the code already appended twice ("Review (G6) (G6)"). */
export function gateLabel(gate: string): string {
  return gate.replace(/(\([^()]+\))(\s*\1)+/g, '$1');
}

function Dot({ status }: { status: string }) {
  return (
    <span
      style={{ width: 6, height: 6, borderRadius: '50%', background: DOT[status] ?? 'var(--mf-c-9a9aaa)', flexShrink: 0, display: 'inline-block' }}
    />
  );
}

function ItemRow({
  row,
  working,
  projectId,
  historyOpen,
  previewOpen,
  onToggleHistory,
  onTogglePreview,
  details,
}: {
  row: CurrentItemRow;
  working: boolean;
  projectId: string;
  historyOpen: boolean;
  previewOpen: boolean;
  onToggleHistory: () => void;
  onTogglePreview: () => void;
  /** Show references, gate and run; off by default to keep the list readable. */
  details: boolean;
}) {
  const evidence = EVIDENCE[row.evidence_state];
  const drafts = working ? row.drafts : [];
  return (
    <div data-testid={`item-row-${row.key}`}>
      {row.revision !== null && row.node_id && (
        <div className="flex items-center gap-3 pl-4 pr-3 hover:bg-surface-high" style={{ minHeight: 40 }}>
          <Dot status={row.validation_status} />
          <span className="material-symbols-outlined flex-shrink-0" style={{ fontSize: 14, color: 'var(--mf-c-9a9aaa)' }}>
            {TYPE_ICON[row.item_type] ?? 'description'}
          </span>
          <Link
            to={`/twin?node=${row.node_id}`}
            className="flex-1 min-w-0 text-sm font-medium hover:underline"
            style={{ color: 'var(--mf-c-e2e2eb)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
          >
            {row.name}
          </Link>
          {details && (
            <span className="font-mono flex-shrink-0" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }}>{row.ref}</span>
          )}
          {details && (row.gate_id || row.run_id) && (
            <span className="font-mono flex-shrink-0" style={{ fontSize: 10, color: 'var(--mf-c-ffb783)' }}>
              {row.gate_id ? `gate ${gateLabel(row.gate_id)}` : ''}
              {row.gate_id && row.run_id ? ' · ' : ''}
              {row.run_id ? `run ${shortId(row.run_id)}` : ''}
            </span>
          )}
          {(details || row.evidence_state !== 'none') && (
            <Badge variant={evidence.variant} className="flex-shrink-0">{evidence.label}</Badge>
          )}
          {/* "created" and "unknown" say nothing about the item; the dot already shows status. */}
          {(details || !QUIET_STATUS.has(row.validation_status)) && <StatusBadge status={row.validation_status} />}
          {row.revision_count > 1 ? (
            <button
              type="button"
              onClick={onToggleHistory}
              aria-expanded={historyOpen}
              className="font-mono flex-shrink-0 rounded hover:underline"
              style={{ fontSize: 10, color: historyOpen ? '#ff5a0a' : 'var(--mf-c-86cfff)', background: 'none', border: 'none', cursor: 'pointer' }}
            >
              {row.revision_count} revisions
            </button>
          ) : (
            <span className="font-mono flex-shrink-0" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)', minWidth: 60, textAlign: 'right' }}>
              {row.updated_at ? formatRelativeTime(row.updated_at) : ''}
            </span>
          )}
          <button
            type="button"
            onClick={onTogglePreview}
            aria-expanded={previewOpen}
            aria-label={`${previewOpen ? 'Hide' : 'Show'} preview of ${row.name}`}
            title="Preview"
            className="flex-shrink-0 rounded"
            style={{ background: previewOpen ? 'var(--mf-c-282a30)' : 'transparent', border: 'none', cursor: 'pointer', padding: 4, color: previewOpen ? '#ff5a0a' : 'var(--mf-c-9a9aaa)' }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 16, verticalAlign: 'middle' }}>
              {previewOpen ? 'visibility_off' : 'visibility'}
            </span>
          </button>
        </div>
      )}
      {drafts.map((d) => (
        <div key={d.node_id} data-testid={`draft-row-${row.key}`} className="flex items-center gap-3 pl-10 pr-3" style={{ minHeight: 32, background: 'rgba(245,158,11,0.05)' }}>
          <Badge variant="warning">DRAFT</Badge>
          <Link to={`/twin?node=${d.node_id}`} className="flex-1 min-w-0 text-xs hover:underline" style={{ color: 'var(--mf-c-e2e2eb)' }}>
            {d.name ?? row.name}
          </Link>
          <span className="font-mono" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }}>{row.key}@{d.revision}</span>
          {d.run_id && (
            <Link to={`/runs/${d.run_id}`} className="font-mono hover:underline" style={{ fontSize: 10, color: 'var(--mf-c-86cfff)' }}>
              run {shortId(d.run_id)}
            </Link>
          )}
        </div>
      ))}
      {historyOpen && (
        <div className="px-4 py-3" style={{ borderTop: '1px solid var(--mf-r-65-72-90-0p2)' }}>
          <ItemHistoryPanel itemKey={row.key} projectId={projectId} onClose={onToggleHistory} />
        </div>
      )}
      {previewOpen && row.node_id && (
        <div className="px-4 py-3" data-testid="project-row-preview" style={{ borderTop: '1px solid var(--mf-r-65-72-90-0p2)', background: 'var(--mf-c-111319)' }}>
          <Suspense fallback={<div className="font-mono" style={{ fontSize: 11, color: 'var(--mf-c-9a9aaa)' }}>Loading preview…</div>}>
            <ProjectRowPreview nodeId={row.node_id} />
          </Suspense>
        </div>
      )}
    </div>
  );
}

function GroupHeader({ label, count }: { label: string; count: number }) {
  return (
    <div className="flex items-center justify-between px-4 py-1.5" style={{ background: 'var(--mf-c-191b22)' }}>
      <span className="font-mono" style={LABEL_STYLE}>{label}</span>
      <span className="font-mono" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }}>{count}</span>
    </div>
  );
}

function RecordLine({ record }: { record: RecordRow }) {
  return (
    <div className="flex items-center gap-2 px-4" style={{ minHeight: 30 }}>
      <Link to={`/twin?node=${record.node_id}`} className="flex-1 min-w-0 text-xs hover:underline" style={{ color: 'var(--mf-c-e2e2eb)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {record.name}
      </Link>
      {record.analysed.map((a) => (
        <span key={a.ref} className="font-mono" style={{ fontSize: 10, color: a.current ? 'var(--mf-c-9a9aaa)' : 'var(--mf-c-f59e0b)' }}>
          {a.ref}
        </span>
      ))}
      {record.created_at && (
        <span className="font-mono" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }}>{formatRelativeTime(record.created_at)}</span>
      )}
    </div>
  );
}

/** Rows a group shows before "Show more"; long lists stay scannable. */
export const GROUP_PREVIEW = 4;

export interface ItemFilter {
  /** An item type, `records`, `other`, or `all`. */
  section: string;
  query: string;
  attention: boolean;
}

/** An item worth a look: failing or warning validation, or evidence gone stale. */
export function needsAttention(row: Pick<CurrentItemRow, 'validation_status' | 'evidence_state'>): boolean {
  return row.validation_status === 'error' || row.validation_status === 'warning' || row.evidence_state === 'out_of_date';
}

function matches(query: string, ...fields: (string | null | undefined)[]): boolean {
  const q = query.trim().toLowerCase();
  return !q || fields.some((f) => (f ?? '').toLowerCase().includes(q));
}

export function filterItems(rows: CurrentItemRow[], filter: ItemFilter): CurrentItemRow[] {
  return rows.filter(
    (r) =>
      (filter.section === 'all' || filter.section === r.item_type) &&
      (!filter.attention || needsAttention(r)) &&
      matches(filter.query, r.name, r.ref, r.key),
  );
}

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className="rounded-full"
      style={{
        fontSize: 11,
        padding: '2px 10px',
        border: `1px solid ${on ? '#ff5a0a' : 'var(--mf-r-65-72-90-0p3)'}`,
        background: on ? 'rgba(255,90,10,0.12)' : 'transparent',
        color: on ? 'var(--mf-c-e2e2eb)' : 'var(--mf-c-9a9aaa)',
        cursor: 'pointer',
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </button>
  );
}

function MoreButton({ hidden, onClick }: { hidden: number; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full text-left px-4 hover:underline"
      style={{ minHeight: 30, fontSize: 12, color: 'var(--mf-c-86cfff)', background: 'none', border: 'none', cursor: 'pointer' }}
    >
      Show {hidden} more
    </button>
  );
}

/** FORGE-526: the project's default view. One row per item at its current
 * revision, grouped by type; records listed as records, with an "Out of
 * date" group for results whose analysed revision is no longer current; a
 * Working toggle for open runs' drafts. History opens per row on request.
 * Search, type chips and "Needs attention" narrow the list, and each group
 * shows its first few rows until asked for the rest. */
export function CurrentItemsPanel({ view, projectId }: { view: CurrentView; projectId: string }) {
  const [working, setWorking] = useState(false);
  const [historyKey, setHistoryKey] = useState<string | null>(null);
  const [previewKey, setPreviewKey] = useState<string | null>(null);
  const [filter, setFilter] = useState<ItemFilter>({ section: 'all', query: '', attention: false });
  const [details, setDetails] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const current = view.items.filter((r) => r.revision !== null || (working && r.drafts.length > 0));
  const visible = filterItems(current, filter);
  const groups = Array.from(new Set(visible.map((r) => r.item_type))).sort();
  const allTypes = Array.from(new Set(current.map((r) => r.item_type))).sort();
  const showRecords = (filter.section === 'all' || filter.section === 'records') && !filter.attention;
  const showOther = (filter.section === 'all' || filter.section === 'other') && !filter.attention;
  const records = showRecords ? view.records.filter((r) => matches(filter.query, r.name)) : [];
  const other = showOther ? view.other.filter((o) => matches(filter.query, o.name, o.type)) : [];
  const outOfDate = records.filter((r) => r.out_of_date);
  const recordTypes = (['design_decision', 'simulation_result', 'evidence'] as const)
    .map((t) => ({ type: t, rows: records.filter((r) => r.record_type === t && !r.out_of_date) }))
    .filter((g) => g.rows.length > 0);
  const itemsShown = filter.section === 'records' || filter.section === 'other' ? [] : visible;
  const nothingAtAll = current.length === 0 && view.other.length === 0;
  const nothingMatches = !nothingAtAll && itemsShown.length === 0 && other.length === 0 && records.length === 0;
  const attentionCount = current.filter(needsAttention).length;
  const filtering = filter.section !== 'all' || filter.attention || filter.query.trim() !== '';

  const set = (patch: Partial<ItemFilter>) => setFilter((f) => ({ ...f, ...patch }));
  function shown<T>(key: string, rows: T[]): T[] {
    return expanded.has(key) || filtering ? rows : rows.slice(0, GROUP_PREVIEW);
  }
  function more(key: string, total: number) {
    return !expanded.has(key) && !filtering && total > GROUP_PREVIEW ? (
      <MoreButton hidden={total - GROUP_PREVIEW} onClick={() => setExpanded((e) => new Set(e).add(key))} />
    ) : null;
  }

  return (
    <div className="glass rounded overflow-hidden" style={PANEL_STYLE} data-testid="current-items">
      <div className="flex items-center justify-between px-4 py-2" style={{ borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}>
        <span className="font-mono" style={{ ...LABEL_STYLE, letterSpacing: '0.1em' }}>Current items</span>
        <div className="flex items-center gap-3">
          {view.counts.superseded_revisions > 0 && (
            <span className="font-mono" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }} title="Older revisions are in each item's history">
              {view.counts.superseded_revisions} older revisions hidden
            </span>
          )}
          <label className="flex items-center gap-1 font-mono cursor-pointer" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }}>
            <input type="checkbox" checked={details} onChange={(e) => setDetails(e.target.checked)} aria-label="Show references, gates and runs" />
            IDs
          </label>
          <label className="flex items-center gap-1 font-mono cursor-pointer" style={{ fontSize: 10, color: working ? 'var(--mf-c-f59e0b)' : 'var(--mf-c-9a9aaa)' }}>
            <input
              type="checkbox"
              checked={working}
              onChange={(e) => setWorking(e.target.checked)}
              aria-label="Show working drafts"
              disabled={view.counts.drafts === 0}
            />
            Working{view.counts.drafts > 0 ? ` (${view.counts.drafts})` : ''}
          </label>
        </div>
      </div>

      {!nothingAtAll && (
        <div className="flex flex-wrap items-center gap-2 px-4 py-2" style={{ borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}>
          <input
            type="search"
            value={filter.query}
            onChange={(e) => set({ query: e.target.value })}
            placeholder="Search items"
            aria-label="Search items"
            className="rounded"
            style={{ flex: '1 1 160px', minWidth: 0, maxWidth: 260, fontSize: 12, padding: '4px 8px', background: 'var(--mf-c-111319)', border: '1px solid var(--mf-r-65-72-90-0p3)', color: 'var(--mf-c-e2e2eb)' }}
          />
          <div className="flex flex-wrap gap-1" role="group" aria-label="Show">
            <Chip on={filter.section === 'all'} onClick={() => set({ section: 'all' })}>
              All
            </Chip>
            {allTypes.map((t) => (
              <Chip key={t} on={filter.section === t} onClick={() => set({ section: t })}>
                {itemTypeLabel(t)} {current.filter((r) => r.item_type === t).length}
              </Chip>
            ))}
            {view.records.length > 0 && (
              <Chip on={filter.section === 'records'} onClick={() => set({ section: 'records' })}>
                Records {view.records.length}
              </Chip>
            )}
            {view.other.length > 0 && (
              <Chip on={filter.section === 'other'} onClick={() => set({ section: 'other' })}>
                Other {view.other.length}
              </Chip>
            )}
          </div>
          <Chip on={filter.attention} onClick={() => set({ attention: !filter.attention })}>
            Needs attention {attentionCount}
          </Chip>
        </div>
      )}

      {nothingAtAll ? (
        <div className="px-4 py-8">
          <EmptyState title="No work products" description="Start a design run or add files to see the parts, documents and results here." />
        </div>
      ) : nothingMatches ? (
        <div className="px-4 py-8">
          <EmptyState
            title="Nothing matches"
            description={filter.attention ? 'No items need attention.' : 'Try another search or show all items.'}
          />
        </div>
      ) : (
        <>
          {groups
            .filter(() => itemsShown.length > 0)
            .map((type) => {
              const rows = visible.filter((r) => r.item_type === type);
              return (
                <div key={type}>
                  <GroupHeader label={itemTypeLabel(type)} count={rows.filter((r) => r.revision !== null).length} />
                  {shown(type, rows).map((row) => (
                    <ItemRow
                      key={row.key}
                      row={row}
                      working={working}
                      projectId={projectId}
                      details={details}
                      historyOpen={historyKey === row.key}
                      previewOpen={previewKey === row.key}
                      onToggleHistory={() => setHistoryKey((k) => (k === row.key ? null : row.key))}
                      onTogglePreview={() => setPreviewKey((k) => (k === row.key ? null : row.key))}
                    />
                  ))}
                  {more(type, rows.length)}
                </div>
              );
            })}
          {other.length > 0 && (
            <div>
              <GroupHeader label="Other work products" count={other.length} />
              {shown('other', other).map((o) => (
                <div key={o.node_id} className="flex items-center gap-3 pl-4 pr-3 hover:bg-surface-high" style={{ minHeight: 36 }}>
                  <Dot status={o.validation_status} />
                  <Link to={`/twin?node=${o.node_id}`} className="flex-1 min-w-0 text-sm hover:underline" style={{ color: 'var(--mf-c-e2e2eb)' }}>{o.name}</Link>
                  <span className="font-mono rounded" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)', background: 'var(--mf-c-282a30)', padding: '1px 5px' }}>{o.type}</span>
                  <StatusBadge status={o.validation_status} />
                </div>
              ))}
              {more('other', other.length)}
            </div>
          )}
        </>
      )}

      {(recordTypes.length > 0 || outOfDate.length > 0) && (
        <div data-testid="records" style={{ borderTop: '1px solid var(--mf-r-65-72-90-0p2)' }}>
          {recordTypes.map((g) => (
            <div key={g.type}>
              <GroupHeader label={RECORD_LABEL[g.type]} count={g.rows.length} />
              {shown(g.type, g.rows).map((r) => <RecordLine key={r.node_id} record={r} />)}
              {more(g.type, g.rows.length)}
            </div>
          ))}
          {outOfDate.length > 0 && (
            <div data-testid="out-of-date">
              <GroupHeader label="Out of date" count={outOfDate.length} />
              <div className="px-4 pt-1 font-mono" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }}>
                These results analysed a revision that is no longer current.
              </div>
              {shown('out-of-date', outOfDate).map((r) => <RecordLine key={r.node_id} record={r} />)}
              {more('out-of-date', outOfDate.length)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
