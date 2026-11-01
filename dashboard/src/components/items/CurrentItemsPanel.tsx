import { lazy, Suspense, useState } from 'react';
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
}: {
  row: CurrentItemRow;
  working: boolean;
  projectId: string;
  historyOpen: boolean;
  previewOpen: boolean;
  onToggleHistory: () => void;
  onTogglePreview: () => void;
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
          <span className="font-mono flex-shrink-0" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }}>{row.ref}</span>
          {(row.gate_id || row.run_id) && (
            <span className="font-mono flex-shrink-0" style={{ fontSize: 10, color: 'var(--mf-c-ffb783)' }}>
              {row.gate_id ? `gate ${row.gate_id}` : ''}
              {row.gate_id && row.run_id ? ' · ' : ''}
              {row.run_id ? `run ${shortId(row.run_id)}` : ''}
            </span>
          )}
          <Badge variant={evidence.variant} className="flex-shrink-0">{evidence.label}</Badge>
          <StatusBadge status={row.validation_status} />
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

/** FORGE-526: the project's default view. One row per item at its current
 * revision, grouped by type; records listed as records, with an "Out of
 * date" group for results whose analysed revision is no longer current; a
 * Working toggle for open runs' drafts. History opens per row on request. */
export function CurrentItemsPanel({ view, projectId }: { view: CurrentView; projectId: string }) {
  const [working, setWorking] = useState(false);
  const [historyKey, setHistoryKey] = useState<string | null>(null);
  const [previewKey, setPreviewKey] = useState<string | null>(null);

  const visible = view.items.filter((r) => r.revision !== null || (working && r.drafts.length > 0));
  const groups = Array.from(new Set(visible.map((r) => r.item_type))).sort();
  const outOfDate = view.records.filter((r) => r.out_of_date);
  const recordTypes = (['design_decision', 'simulation_result', 'evidence'] as const)
    .map((t) => ({ type: t, rows: view.records.filter((r) => r.record_type === t && !r.out_of_date) }))
    .filter((g) => g.rows.length > 0);
  const empty = visible.length === 0 && view.other.length === 0;

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

      {empty ? (
        <div className="px-4 py-8">
          <EmptyState title="No work products" description="Run an agent to create work products." />
        </div>
      ) : (
        <>
          {groups.map((type) => {
            const rows = visible.filter((r) => r.item_type === type);
            return (
              <div key={type}>
                <GroupHeader label={itemTypeLabel(type)} count={rows.filter((r) => r.revision !== null).length} />
                {rows.map((row) => (
                  <ItemRow
                    key={row.key}
                    row={row}
                    working={working}
                    projectId={projectId}
                    historyOpen={historyKey === row.key}
                    previewOpen={previewKey === row.key}
                    onToggleHistory={() => setHistoryKey((k) => (k === row.key ? null : row.key))}
                    onTogglePreview={() => setPreviewKey((k) => (k === row.key ? null : row.key))}
                  />
                ))}
              </div>
            );
          })}
          {view.other.length > 0 && (
            <div>
              <GroupHeader label="Other work products" count={view.other.length} />
              {view.other.map((o) => (
                <div key={o.node_id} className="flex items-center gap-3 pl-4 pr-3 hover:bg-surface-high" style={{ minHeight: 36 }}>
                  <Dot status={o.validation_status} />
                  <Link to={`/twin?node=${o.node_id}`} className="flex-1 min-w-0 text-sm hover:underline" style={{ color: 'var(--mf-c-e2e2eb)' }}>{o.name}</Link>
                  <span className="font-mono rounded" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)', background: 'var(--mf-c-282a30)', padding: '1px 5px' }}>{o.type}</span>
                  <StatusBadge status={o.validation_status} />
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {(recordTypes.length > 0 || outOfDate.length > 0) && (
        <div data-testid="records" style={{ borderTop: '1px solid var(--mf-r-65-72-90-0p2)' }}>
          {recordTypes.map((g) => (
            <div key={g.type}>
              <GroupHeader label={RECORD_LABEL[g.type]} count={g.rows.length} />
              {g.rows.map((r) => <RecordLine key={r.node_id} record={r} />)}
            </div>
          ))}
          {outOfDate.length > 0 && (
            <div data-testid="out-of-date">
              <GroupHeader label="Out of date" count={outOfDate.length} />
              <div className="px-4 pt-1 font-mono" style={{ fontSize: 10, color: 'var(--mf-c-9a9aaa)' }}>
                These results analysed a revision that is no longer current.
              </div>
              {outOfDate.map((r) => <RecordLine key={r.node_id} record={r} />)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
