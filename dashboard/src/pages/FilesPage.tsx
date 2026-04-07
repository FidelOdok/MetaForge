import { useState } from 'react';
import { useAllLinks, useDeleteLink, useSyncNode } from '../hooks/use-links';
import type { FileLink, FileLinkStatus, FileLinkTool } from '../types/twin';

// ── Status dot colours ────────────────────────────────────────────────────────
const STATUS_DOT_COLOR: Record<FileLinkStatus, string> = {
  synced:       '#3dd68c',
  changed:      '#f59e0b',
  disconnected: '#9a9aaa',
};

// ── Tool chip colours ─────────────────────────────────────────────────────────
const TOOL_CHIP: Record<string, { color: string; bg: string }> = {
  kicad:  { color: '#86cfff', bg: 'rgba(134,207,255,0.1)' },
  freecad:{ color: '#e67e22', bg: 'rgba(230,126,34,0.1)'  },
  spice:  { color: '#3dd68c', bg: 'rgba(61,214,140,0.1)'  },
  other:  { color: '#9a9aaa', bg: 'rgba(154,154,170,0.1)' },
};

function toolKey(tool: FileLinkTool): string {
  return tool === 'none' ? 'other' : tool;
}

// ── Tool chip ─────────────────────────────────────────────────────────────────
function ToolChip({ tool }: { tool: FileLinkTool }) {
  const key = toolKey(tool);
  const entry = TOOL_CHIP[key] ?? TOOL_CHIP['other']!;
  const { color, bg } = entry;
  return (
    <span
      style={{
        fontFamily: 'monospace',
        fontSize: 10,
        color,
        background: bg,
        padding: '2px 6px',
        borderRadius: 3,
        flexShrink: 0,
        letterSpacing: '0.06em',
        textTransform: 'uppercase',
      }}
    >
      {key}
    </span>
  );
}

// ── Status dot ────────────────────────────────────────────────────────────────
function StatusDot({ status }: { status: FileLinkStatus }) {
  return (
    <span
      aria-hidden="true"
      style={{
        display: 'inline-block',
        width: 6,
        height: 6,
        borderRadius: '50%',
        background: STATUS_DOT_COLOR[status],
        flexShrink: 0,
      }}
    />
  );
}

// ── Icon button (sync / unlink) ───────────────────────────────────────────────
function IconBtn({
  icon,
  title,
  onClick,
  disabled,
}: {
  icon: string;
  title: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  const [hover, setHover] = useState(false);
  return (
    <button
      title={title}
      onClick={onClick}
      disabled={disabled}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: 24,
        height: 24,
        borderRadius: 4,
        border: 'none',
        cursor: disabled ? 'default' : 'pointer',
        background: hover && !disabled ? '#282a30' : 'transparent',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 0,
        opacity: disabled ? 0.4 : 1,
        transition: 'background 0.15s',
      }}
    >
      <span
        className="material-symbols-outlined"
        style={{ fontSize: 14, color: '#9a9aaa' }}
      >
        {icon}
      </span>
    </button>
  );
}

// ── File link row ─────────────────────────────────────────────────────────────
function FileLinkRow({ link }: { link: FileLink }) {
  const deleteMutation = useDeleteLink(link.node_id);
  const syncMutation   = useSyncNode(link.node_id);
  const relTime = link.last_synced_at ? formatRelative(link.last_synced_at) : '—';

  return (
    <div
      style={{
        height: 40,
        padding: '0 16px',
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        borderBottom: '1px solid rgba(65,72,90,0.08)',
      }}
    >
      <ToolChip tool={link.tool} />
      <span
        title={link.file_path}
        style={{
          flex: 1,
          fontSize: 13,
          color: '#d4d4d8',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          minWidth: 0,
        }}
      >
        {link.file_path}
      </span>
      <StatusDot status={link.status} />
      <span
        style={{
          fontFamily: 'monospace',
          fontSize: 10,
          color: '#9a9aaa',
          whiteSpace: 'nowrap',
          flexShrink: 0,
        }}
      >
        {relTime}
      </span>
      <div style={{ display: 'flex', gap: 2, flexShrink: 0 }}>
        {link.status !== 'disconnected' && (
          <IconBtn
            icon="sync"
            title="Sync"
            onClick={() => syncMutation.mutate()}
            disabled={syncMutation.isPending}
          />
        )}
        <IconBtn
          icon="link_off"
          title="Unlink"
          onClick={() => deleteMutation.mutate()}
          disabled={deleteMutation.isPending}
        />
      </div>
    </div>
  );
}

// ── Glass panel wrapper ───────────────────────────────────────────────────────
const GLASS: React.CSSProperties = {
  background: 'rgba(30,31,38,0.85)',
  backdropFilter: 'blur(16px)',
  border: '1px solid rgba(65,72,90,0.2)',
  borderRadius: 4,
};

// ── Panel header label ────────────────────────────────────────────────────────
function PanelLabel({ children }: { children: React.ReactNode }) {
  return (
    <span
      style={{
        fontFamily: 'monospace',
        fontSize: 10,
        color: '#9a9aaa',
        textTransform: 'uppercase',
        letterSpacing: '0.1em',
      }}
    >
      {children}
    </span>
  );
}

// ── Static pipeline placeholder rows ─────────────────────────────────────────
const STATIC_PIPELINE = [
  { path: 'PRD.md',                  tool: 'other',  status: 'synced'       as FileLinkStatus },
  { path: 'constraints.json',        tool: 'other',  status: 'synced'       as FileLinkStatus },
  { path: 'bom.csv',                 tool: 'other',  status: 'changed'      as FileLinkStatus },
  { path: 'schematic.kicad_sch',     tool: 'kicad',  status: 'disconnected' as FileLinkStatus },
];

// ── Page ──────────────────────────────────────────────────────────────────────
export function FilesPage() {
  const { data: links, isLoading } = useAllLinks();
  const [filterTool, setFilterTool] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState('');

  const synced       = links?.filter(l => l.status === 'synced').length       ?? 0;
  const changed      = links?.filter(l => l.status === 'changed').length      ?? 0;
  const disconnected = links?.filter(l => l.status === 'disconnected').length ?? 0;
  const total        = links?.length ?? 0;

  const filteredLinks = (links ?? []).filter(l => {
    const matchTool = filterTool === 'all' || toolKey(l.tool) === filterTool;
    const matchSearch = searchQuery === '' ||
      l.file_path.toLowerCase().includes(searchQuery.toLowerCase());
    return matchTool && matchSearch;
  });

  // By-tool breakdown
  const toolCounts: Record<string, number> = { kicad: 0, freecad: 0, spice: 0, other: 0 };
  if (total > 0) {
    for (const l of links!) {
      const k = toolKey(l.tool);
      toolCounts[k] = (toolCounts[k] ?? 0) + 1;
    }
  } else {
    // static fallback
    toolCounts.kicad = 12; toolCounts.freecad = 8; toolCounts.spice = 4; toolCounts.other = 6;
  }
  const toolTotal = Object.values(toolCounts).reduce((a, b) => a + b, 0);

  const FILTER_CHIPS = ['all', 'kicad', 'freecad', 'spice', 'other'];

  // Pipeline rows: up to 7 most recent real links, or static if none
  const pipelineRows = total > 0
    ? [...(links!)]
        .sort((a, b) => {
          const ta = a.last_synced_at ? new Date(a.last_synced_at).getTime() : 0;
          const tb = b.last_synced_at ? new Date(b.last_synced_at).getTime() : 0;
          return tb - ta;
        })
        .slice(0, 7)
    : null;

  return (
    <div>

      {/* ── Page header ─────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
          <span style={{ fontSize: 18, fontWeight: 500, color: '#e8e8ed' }}>Files</span>
          <span style={{ fontFamily: 'monospace', fontSize: 11, color: '#9a9aaa' }}>
            {total} linked · source file registry
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {synced > 0 && (
            <span style={{ fontFamily: 'monospace', fontSize: 10, color: '#3dd68c', background: 'rgba(61,214,140,0.1)', padding: '2px 8px', borderRadius: 4 }}>
              {synced} synced
            </span>
          )}
          {changed > 0 && (
            <span style={{ fontFamily: 'monospace', fontSize: 10, color: '#f59e0b', background: 'rgba(245,158,11,0.12)', padding: '2px 8px', borderRadius: 4 }}>
              {changed} changed
            </span>
          )}
          {disconnected > 0 && (
            <span style={{ fontFamily: 'monospace', fontSize: 10, color: '#9a9aaa', background: 'rgba(154,154,170,0.1)', padding: '2px 8px', borderRadius: 4 }}>
              {disconnected} disconnected
            </span>
          )}
          <button
            title="Refresh"
            onClick={() => window.location.reload()}
            style={{
              width: 28,
              height: 28,
              border: 'none',
              background: 'transparent',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              borderRadius: 4,
              color: '#9a9aaa',
            }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>refresh</span>
          </button>
        </div>
      </div>

      {/* ── Search section ──────────────────────────────────────────────────── */}
      <div style={{ marginBottom: 12 }}>
        {/* Glass pill search */}
        <div
          style={{
            height: 44,
            padding: '0 16px',
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            background: 'rgba(40,42,48,0.92)',
            backdropFilter: 'blur(16px)',
            borderRadius: 9999,
            border: '1px solid rgba(65,72,90,0.2)',
          }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 16, color: '#9a9aaa', flexShrink: 0 }}>
            search
          </span>
          <input
            type="text"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder="Search linked files..."
            style={{
              flex: 1,
              background: 'transparent',
              border: 'none',
              outline: 'none',
              fontSize: 13,
              color: '#e8e8ed',
              fontFamily: 'Inter, sans-serif',
            }}
          />
          <span
            style={{
              fontFamily: 'monospace',
              fontSize: 10,
              color: '#9a9aaa',
              background: 'rgba(65,72,90,0.3)',
              padding: '2px 6px',
              borderRadius: 3,
              flexShrink: 0,
            }}
          >
            /
          </span>
        </div>

        {/* Filter chips */}
        <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
          {FILTER_CHIPS.map(chip => {
            const active = filterTool === chip;
            return (
              <button
                key={chip}
                onClick={() => setFilterTool(chip)}
                style={{
                  fontFamily: 'monospace',
                  fontSize: 10,
                  textTransform: 'uppercase',
                  letterSpacing: '0.06em',
                  padding: '4px 10px',
                  borderRadius: 4,
                  border: 'none',
                  cursor: 'pointer',
                  background: active ? '#e67e22' : 'rgba(30,31,38,0.85)',
                  color: active ? '#000' : '#9a9aaa',
                  transition: 'background 0.15s, color 0.15s',
                }}
              >
                {chip}
              </button>
            );
          })}
        </div>
      </div>

      {/* ── Two-column grid ─────────────────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 280px', gap: 12 }}>

        {/* ── Left column ───────────────────────────────────────────────────── */}
        <div>

          {/* FILE LINKS panel */}
          <div style={GLASS}>
            {/* Panel header */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '8px 16px',
                borderBottom: '1px solid rgba(65,72,90,0.2)',
              }}
            >
              <PanelLabel>File Links</PanelLabel>
              <span style={{ fontFamily: 'monospace', fontSize: 10, color: '#9a9aaa' }}>
                {filteredLinks.length} of {total}
              </span>
            </div>

            {/* Rows */}
            {isLoading ? (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '40px 0', gap: 8 }}>
                <span className="material-symbols-outlined" style={{ fontSize: 20, color: '#9a9aaa' }}>
                  progress_activity
                </span>
                <span style={{ fontFamily: 'monospace', fontSize: 11, color: '#9a9aaa' }}>Loading…</span>
              </div>
            ) : filteredLinks.length === 0 ? (
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 8,
                  padding: '48px 0',
                  minHeight: 160,
                }}
              >
                <span className="material-symbols-outlined" style={{ fontSize: 32, color: '#9a9aaa', opacity: 0.4 }}>
                  link_off
                </span>
                <span style={{ fontSize: 13, color: '#9a9aaa' }}>No source files linked yet</span>
              </div>
            ) : (
              filteredLinks.map(link => <FileLinkRow key={link.id} link={link} />)
            )}
          </div>

          {/* SYNC PIPELINE panel */}
          <div style={{ ...GLASS, marginTop: 12 }}>
            <div
              style={{
                padding: '8px 16px',
                borderBottom: '1px solid rgba(65,72,90,0.2)',
              }}
            >
              <PanelLabel>Sync Pipeline</PanelLabel>
            </div>

            {pipelineRows
              ? pipelineRows.map(link => (
                  <div
                    key={link.id}
                    style={{
                      height: 36,
                      padding: '0 16px',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 10,
                      borderBottom: '1px solid rgba(65,72,90,0.06)',
                    }}
                  >
                    <span
                      title={link.file_path}
                      style={{
                        fontFamily: 'monospace',
                        fontSize: 11,
                        color: '#d4d4d8',
                        flex: 1,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        minWidth: 0,
                      }}
                    >
                      {link.file_path}
                    </span>
                    <ToolChip tool={link.tool} />
                    <StatusDot status={link.status} />
                    <span style={{ fontFamily: 'monospace', fontSize: 10, color: '#9a9aaa', whiteSpace: 'nowrap' }}>
                      {link.last_synced_at ? formatRelative(link.last_synced_at) : '—'}
                    </span>
                  </div>
                ))
              : STATIC_PIPELINE.map((row, i) => (
                  <div
                    key={i}
                    style={{
                      height: 36,
                      padding: '0 16px',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 10,
                      borderBottom: '1px solid rgba(65,72,90,0.06)',
                    }}
                  >
                    <span
                      style={{
                        fontFamily: 'monospace',
                        fontSize: 11,
                        color: '#d4d4d8',
                        flex: 1,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {row.path}
                    </span>
                    <span
                      style={{
                        fontFamily: 'monospace',
                        fontSize: 10,
                        color: TOOL_CHIP[row.tool]?.color ?? '#9a9aaa',
                        background: TOOL_CHIP[row.tool]?.bg ?? 'rgba(154,154,170,0.1)',
                        padding: '2px 6px',
                        borderRadius: 3,
                        textTransform: 'uppercase',
                        letterSpacing: '0.06em',
                      }}
                    >
                      {row.tool}
                    </span>
                    <StatusDot status={row.status} />
                    <span style={{ fontFamily: 'monospace', fontSize: 10, color: '#9a9aaa' }}>—</span>
                  </div>
                ))
            }
          </div>
        </div>

        {/* ── Right column ──────────────────────────────────────────────────── */}
        <div>

          {/* BY TOOL panel */}
          <div style={{ ...GLASS, padding: 16 }}>
            <div style={{ marginBottom: 12 }}>
              <PanelLabel>By Tool</PanelLabel>
            </div>
            {(['kicad', 'freecad', 'spice', 'other'] as const).map(key => {
              const count = toolCounts[key] ?? 0;
              const pct = toolTotal > 0 ? (count / toolTotal) * 100 : 0;
              const color = TOOL_CHIP[key]?.color ?? '#9a9aaa';
              return (
                <div key={key} style={{ marginBottom: 12 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span style={{ fontFamily: 'monospace', fontSize: 10, color: '#9a9aaa', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                      {key}
                    </span>
                    <span style={{ fontFamily: 'monospace', fontSize: 10, color: '#9a9aaa' }}>{count}</span>
                  </div>
                  <div style={{ height: 4, background: 'rgba(65,72,90,0.3)', borderRadius: 2, overflow: 'hidden' }}>
                    <div
                      style={{
                        height: '100%',
                        width: `${pct}%`,
                        background: color,
                        borderRadius: 2,
                        transition: 'width 0.4s ease',
                      }}
                    />
                  </div>
                </div>
              );
            })}
          </div>

          {/* SYNC STATUS panel */}
          <div style={{ ...GLASS, padding: 16, marginTop: 12 }}>
            <div style={{ marginBottom: 12 }}>
              <PanelLabel>Sync Status</PanelLabel>
            </div>
            {([
              { key: 'synced'       as FileLinkStatus, label: 'Synced',       color: '#3dd68c', count: synced       },
              { key: 'changed'      as FileLinkStatus, label: 'Changed',      color: '#f59e0b', count: changed      },
              { key: 'disconnected' as FileLinkStatus, label: 'Disconnected', color: '#9a9aaa', count: disconnected },
            ]).map(({ key, label, color, count }) => {
              const pct = total > 0 ? (count / total) * 100 : 0;
              return (
                <div key={key} style={{ marginBottom: 12 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span style={{ fontSize: 12, color: '#d4d4d8' }}>{label}</span>
                    <span style={{ fontFamily: 'monospace', fontSize: 11, color: '#9a9aaa' }}>
                      {total > 0 ? count : '—'}
                    </span>
                  </div>
                  <div style={{ height: 4, background: 'rgba(65,72,90,0.3)', borderRadius: 2, overflow: 'hidden' }}>
                    <div
                      style={{
                        height: '100%',
                        width: `${pct}%`,
                        background: color,
                        borderRadius: 2,
                        transition: 'width 0.4s ease',
                      }}
                    />
                  </div>
                </div>
              );
            })}
          </div>

        </div>
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
