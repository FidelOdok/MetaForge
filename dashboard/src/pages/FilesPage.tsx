import { useAllLinks, useDeleteLink, useSyncNode } from '../hooks/use-links';
import { Button } from '../components/ui/Button';
import type { FileLink, FileLinkStatus, FileLinkTool } from '../types/twin';

// ── Status dot colours per spec ──────────────────────────────────────────────
const STATUS_DOT_COLOR: Record<FileLinkStatus, string> = {
  synced:       '#e67e22', // primary-container (amber-orange per KC spec)
  changed:      '#f59e0b', // warning
  disconnected: '#9a9aaa', // on-surface-variant
};

// ── Tool label chip ───────────────────────────────────────────────────────────
function ToolChip({ tool }: { tool: FileLinkTool }) {
  return (
    <span
      className="font-mono text-[10px] text-on-surface-variant px-1.5 py-0.5 rounded flex-shrink-0 tracking-wider uppercase"
      style={{ background: '#282a30' }}
    >
      {tool === 'none' ? 'OTHER' : tool.toUpperCase()}
    </span>
  );
}

// ── Inline status dot ─────────────────────────────────────────────────────────
function StatusDot({ status }: { status: FileLinkStatus }) {
  return (
    <span
      className="flex-shrink-0 inline-block rounded-full"
      style={{
        width: 6,
        height: 6,
        background: STATUS_DOT_COLOR[status],
      }}
      aria-hidden="true"
    />
  );
}

// ── Single link row ───────────────────────────────────────────────────────────
function LinkRow({ link }: { link: FileLink }) {
  const deleteLinkMutation = useDeleteLink(link.node_id);
  const syncMutation = useSyncNode(link.node_id);

  const relTime = link.last_synced_at
    ? formatRelative(link.last_synced_at)
    : '—';

  return (
    <tr
      className="hover:bg-surface-high cursor-pointer"
      style={{ height: 36, borderBottom: '1px solid rgba(65,72,90,0.1)' }}
    >
      {/* Node ID */}
      <td
        className="px-4 font-mono text-xs text-on-surface-variant whitespace-nowrap"
        title={link.node_id}
      >
        {link.node_id.slice(0, 10)}…
      </td>

      {/* File path */}
      <td className="px-4 max-w-xs">
        <span
          className="block truncate font-mono text-xs text-on-surface"
          title={link.file_path}
        >
          {link.file_path}
        </span>
      </td>

      {/* Tool chip */}
      <td className="px-4 whitespace-nowrap">
        <ToolChip tool={link.tool} />
      </td>

      {/* Status dot */}
      <td className="px-4 whitespace-nowrap">
        <div className="flex items-center gap-2">
          <StatusDot status={link.status} />
          <span className="font-mono text-[10px] text-on-surface-variant capitalize">
            {link.status}
          </span>
        </div>
      </td>

      {/* Last synced */}
      <td className="px-4 font-mono text-[10px] text-on-surface-variant whitespace-nowrap text-right">
        {relTime}
      </td>

      {/* Actions */}
      <td className="px-4 whitespace-nowrap">
        <div className="flex items-center gap-1.5">
          {link.status !== 'disconnected' && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => syncMutation.mutate()}
              disabled={syncMutation.isPending}
            >
              {syncMutation.isPending ? (
                <span className="material-symbols-outlined" style={{ fontSize: 13 }}>
                  sync
                </span>
              ) : (
                <span className="material-symbols-outlined" style={{ fontSize: 13 }}>
                  sync
                </span>
              )}
              <span className="ml-1">{syncMutation.isPending ? 'Syncing…' : 'Sync'}</span>
            </Button>
          )}
          <Button
            variant="danger"
            size="sm"
            onClick={() => deleteLinkMutation.mutate()}
            disabled={deleteLinkMutation.isPending}
          >
            {deleteLinkMutation.isPending ? 'Removing…' : 'Unlink'}
          </Button>
        </div>
      </td>
    </tr>
  );
}

// ── Empty state ───────────────────────────────────────────────────────────────
function EmptyState() {
  return (
    <tr>
      <td colSpan={6}>
        <div
          className="flex flex-col items-center justify-center gap-3 py-16"
          style={{ minHeight: 200 }}
        >
          <span
            className="material-symbols-outlined text-on-surface-variant"
            style={{ fontSize: 36, opacity: 0.4 }}
          >
            link_off
          </span>
          <p className="text-sm font-medium text-on-surface-variant">
            No source files linked yet
          </p>
          <p className="font-mono text-[10px] text-on-surface-variant opacity-60 text-center max-w-xs">
            Open a Digital Twin node and use the Link Panel to connect a source file.
          </p>
        </div>
      </td>
    </tr>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────
export function FilesPage() {
  const { data: links, isLoading } = useAllLinks();

  const synced       = links?.filter(l => l.status === 'synced').length       ?? 0;
  const changed      = links?.filter(l => l.status === 'changed').length      ?? 0;
  const disconnected = links?.filter(l => l.status === 'disconnected').length ?? 0;
  const total        = links?.length ?? 0;

  return (
    <div className="p-6">

      {/* ── Page header ───────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <span className="text-lg font-medium text-on-surface">Files</span>
          <span className="font-mono text-xs text-on-surface-variant">
            {total} linked · source file registry
          </span>
        </div>
        <div className="flex items-center gap-2">
          {/* Summary pills */}
          {synced > 0 && (
            <span
              className="font-mono text-[10px] text-on-surface-variant rounded px-2 py-0.5 tracking-wider"
              style={{ background: '#282a30' }}
            >
              {synced} synced
            </span>
          )}
          {changed > 0 && (
            <span
              className="font-mono text-[10px] rounded px-2 py-0.5 tracking-wider"
              style={{ background: 'rgba(245,158,11,0.12)', color: '#f59e0b' }}
            >
              {changed} changed
            </span>
          )}
          {disconnected > 0 && (
            <span
              className="font-mono text-[10px] text-on-surface-variant rounded px-2 py-0.5 tracking-wider"
              style={{ background: 'rgba(154,154,170,0.1)' }}
            >
              {disconnected} disconnected
            </span>
          )}
          <button
            className="flex items-center justify-center rounded hover:bg-surface-high text-on-surface-variant"
            style={{ width: 28, height: 28 }}
            onClick={() => window.location.reload()}
            title="Refresh"
          >
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
              refresh
            </span>
          </button>
        </div>
      </div>

      {/* ── File table ────────────────────────────────────────────────────── */}
      <div
        className="rounded overflow-hidden"
        style={{
          background: 'rgba(30,31,38,0.85)',
          border: '1px solid rgba(65,72,90,0.2)',
        }}
      >
        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <span className="material-symbols-outlined text-on-surface-variant animate-spin" style={{ fontSize: 24 }}>
              progress_activity
            </span>
            <span className="ml-2 font-mono text-xs text-on-surface-variant">Loading…</span>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr
                  className="bg-surface-low"
                  style={{ borderBottom: '1px solid rgba(65,72,90,0.3)' }}
                >
                  <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-on-surface-variant whitespace-nowrap">
                    Node ID
                  </th>
                  <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-on-surface-variant">
                    File Path
                  </th>
                  <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-on-surface-variant whitespace-nowrap">
                    Tool
                  </th>
                  <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-on-surface-variant whitespace-nowrap">
                    Status
                  </th>
                  <th className="px-4 py-2.5 text-right font-mono text-[10px] uppercase tracking-widest text-on-surface-variant whitespace-nowrap">
                    Last Synced
                  </th>
                  <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-on-surface-variant whitespace-nowrap">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {!links || links.length === 0 ? (
                  <EmptyState />
                ) : (
                  links.map((link) => <LinkRow key={link.id} link={link} />)
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

    </div>
  );
}

// ── Utility ───────────────────────────────────────────────────────────────────
function formatRelative(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const min  = Math.floor(diff / 60_000);
  if (min < 1)   return 'just now';
  if (min < 60)  return `${min} min ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24)   return `${hr} hr ago`;
  const d  = Math.floor(hr / 24);
  return `${d}d ago`;
}
