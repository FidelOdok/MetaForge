import { useState, useRef, useCallback, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '../components/ui/Button';
import { StatusBadge } from '../components/shared/StatusBadge';
import { formatRelativeTime } from '../utils/format-time';
import { useTwinNodes, useTwinNode, useTwinRelationships } from '../hooks/use-twin';
import { TwinGraphCanvas } from '../components/viewer/TwinGraphCanvas';
import { useNodeLink, useCreateLink, useDeleteLink, useSyncNode } from '../hooks/use-links';
import type { FileLinkTool } from '../types/twin';
import { useScopedChat } from '../hooks/use-scoped-chat';
import { NodeChatPanel } from '../components/chat/integrations/NodeChatPanel';
import { R3FViewer } from '../components/viewer/R3FViewer';
import { ComponentTree } from '../components/viewer/ComponentTree';
import { BomAnnotationPanel } from '../components/viewer/BomAnnotationPanel';
import { ExplodedViewControls } from '../components/viewer/ExplodedViewControls';
import { useViewerStore } from '../store/viewer-store';
import { useUploadAndConvert } from '../hooks/use-conversion';
import { getMockManifest, getMockGlbUrl } from '../api/endpoints/convert';
import { getNodeModel } from '../api/endpoints/twin';
import type { TwinNode } from '../types/twin';
import type { ModelManifest, PartInfo, PartTreeNode } from '../types/viewer';

// ── KC tokens ────────────────────────────────────────────────────────────────
const KC = {
  surface: '#111319',
  surfaceLow: '#191b22',
  surfaceContainer: 'rgba(30,31,38,0.88)',
  surfaceHigh: '#282a30',
  border: 'rgba(65,72,90,0.2)',
  borderMid: 'rgba(65,72,90,0.3)',
  onSurface: '#e2e2eb',
  onSurfaceVariant: '#9a9aaa',
  orange: '#e67e22',
  orangeFaint: 'rgba(230,126,34,0.15)',
  orangeBorder: 'rgba(230,126,34,0.45)',
  teal: '#86cfff',
  green: '#3dd68c',
  statusBar: 'rgba(12,14,20,0.95)',
} as const;

// ── Icon map ─────────────────────────────────────────────────────────────────
const NODE_ICONS: Record<TwinNode['type'], string> = {
  work_product: 'description',
  constraint: 'rule',
  relationship: 'link',
  version: 'label',
};

// ── Small helpers ─────────────────────────────────────────────────────────────

function GlassPanel({
  children,
  style,
  className,
}: {
  children: React.ReactNode;
  style?: React.CSSProperties;
  className?: string;
}) {
  return (
    <div
      className={className}
      style={{
        background: KC.surfaceContainer,
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        border: `1px solid ${KC.border}`,
        borderRadius: 6,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

function ToolBtn({
  icon,
  active,
  title,
  onClick,
}: {
  icon: string;
  active?: boolean;
  title: string;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      style={{
        width: 48,
        height: 48,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: active ? 'rgba(40,42,48,0.9)' : 'transparent',
        borderLeft: `2px solid ${active ? KC.orange : 'transparent'}`,
        border: 'none',
        borderLeftWidth: 2,
        borderLeftStyle: 'solid',
        borderLeftColor: active ? KC.orange : 'transparent',
        color: active ? KC.orange : KC.onSurfaceVariant,
        cursor: 'pointer',
        transition: 'color 0.12s, background 0.12s',
        outline: 'none',
      }}
      onMouseEnter={(e) => {
        if (!active) (e.currentTarget as HTMLButtonElement).style.background = KC.surfaceHigh;
      }}
      onMouseLeave={(e) => {
        if (!active) (e.currentTarget as HTMLButtonElement).style.background = 'transparent';
      }}
    >
      <span className="material-symbols-outlined" style={{ fontSize: 18 }}>{icon}</span>
    </button>
  );
}

// ── NodeLinkSection ───────────────────────────────────────────────────────────
const TOOL_OPTIONS: { value: FileLinkTool; label: string }[] = [
  { value: 'kicad',    label: 'KiCad' },
  { value: 'freecad',  label: 'FreeCAD' },
  { value: 'cadquery', label: 'CadQuery' },
  { value: 'none',     label: 'Other' },
];

const SYNC_COLORS: Record<string, string> = {
  synced:       '#3dd68c',
  changed:      '#f5a623',
  disconnected: '#9a9aaa',
};

function NodeLinkSection({ nodeId }: { nodeId: string }) {
  const { data: link, isLoading } = useNodeLink(nodeId);
  const createMutation  = useCreateLink(nodeId);
  const deleteMutation  = useDeleteLink(nodeId);
  const syncMutation    = useSyncNode(nodeId);

  const [filePath, setFilePath] = useState('');
  const [tool, setTool]         = useState<FileLinkTool>('kicad');
  const [watch, setWatch]       = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [err, setErr]           = useState<string | null>(null);

  const inputStyle: React.CSSProperties = {
    width: '100%', boxSizing: 'border-box',
    background: KC.surfaceHigh, border: `1px solid ${KC.borderMid}`,
    borderRadius: 4, color: KC.onSurface,
    fontSize: 11, fontFamily: 'monospace',
    padding: '5px 8px', outline: 'none',
  };

  if (isLoading) return null;

  if (!link) {
    return (
      <div className="px-3 py-2 flex-shrink-0" style={{ borderBottom: `1px solid ${KC.border}` }}>
        <div className="font-mono uppercase mb-2" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}>
          Link Source File
        </div>
        {err && (
          <div style={{ background: 'rgba(231,76,60,0.12)', border: '1px solid rgba(231,76,60,0.3)', borderRadius: 4, padding: '4px 8px', fontSize: 11, color: '#e74c3c', marginBottom: 8 }}>
            {err}
          </div>
        )}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <input
            style={inputStyle}
            placeholder="/path/to/file.kicad_sch"
            value={filePath}
            onChange={(e) => setFilePath(e.target.value)}
          />
          <select
            style={{ ...inputStyle, cursor: 'pointer' }}
            value={tool}
            onChange={(e) => setTool(e.target.value as FileLinkTool)}
          >
            {TOOL_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: KC.onSurfaceVariant, cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={watch}
              onChange={(e) => setWatch(e.target.checked)}
              style={{ accentColor: KC.orange }}
            />
            Watch for changes
          </label>
          <button
            type="button"
            disabled={createMutation.isPending || !filePath.trim()}
            onClick={() => {
              setErr(null);
              createMutation.mutate(
                { source_path: filePath.trim(), tool, watch },
                {
                  onSuccess: () => { setFilePath(''); },
                  onError: (e) => setErr(e instanceof Error ? e.message : 'Failed to link'),
                },
              );
            }}
            style={{
              background: KC.orange, color: KC.surface, border: 'none',
              borderRadius: 4, padding: '5px 10px', fontSize: 11, fontWeight: 600,
              cursor: createMutation.isPending || !filePath.trim() ? 'not-allowed' : 'pointer',
              opacity: createMutation.isPending || !filePath.trim() ? 0.5 : 1,
              fontFamily: 'monospace',
            }}
          >
            {createMutation.isPending ? 'Linking…' : 'Link file'}
          </button>
        </div>
      </div>
    );
  }

  const syncColor = SYNC_COLORS[link.sync_status ?? 'disconnected'] ?? KC.onSurfaceVariant;

  return (
    <div className="px-3 py-2 flex-shrink-0" style={{ borderBottom: `1px solid ${KC.border}` }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
        <div className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}>
          Source File
        </div>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 10, color: syncColor, fontFamily: 'monospace' }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: syncColor, display: 'inline-block' }} />
          {link.sync_status ?? 'unknown'}
        </span>
      </div>
      <div style={{ fontSize: 11, color: KC.onSurface, fontFamily: 'monospace', wordBreak: 'break-all', marginBottom: 6 }}>
        {link.source_path}
      </div>
      <div style={{ fontSize: 10, color: KC.onSurfaceVariant, marginBottom: 8, fontFamily: 'monospace' }}>
        {link.tool} {link.watch ? '· watching' : ''}{link.last_synced_at ? ` · synced ${new Date(link.last_synced_at).toLocaleTimeString()}` : ''}
      </div>
      {err && (
        <div style={{ background: 'rgba(231,76,60,0.12)', border: '1px solid rgba(231,76,60,0.3)', borderRadius: 4, padding: '4px 8px', fontSize: 11, color: '#e74c3c', marginBottom: 6 }}>
          {err}
        </div>
      )}
      <div style={{ display: 'flex', gap: 6 }}>
        {link.sync_status !== 'disconnected' && (
          <button
            type="button"
            disabled={syncMutation.isPending}
            onClick={() => {
              setErr(null);
              syncMutation.mutate(undefined, {
                onError: (e) => setErr(e instanceof Error ? e.message : 'Sync failed'),
              });
            }}
            style={{
              flex: 1, background: link.sync_status === 'changed' ? KC.orange : KC.surfaceHigh,
              color: link.sync_status === 'changed' ? KC.surface : KC.onSurface,
              border: `1px solid ${KC.borderMid}`, borderRadius: 4,
              padding: '4px 8px', fontSize: 11, fontFamily: 'monospace', cursor: 'pointer',
              opacity: syncMutation.isPending ? 0.5 : 1,
            }}
          >
            {syncMutation.isPending ? 'Syncing…' : 'Sync now'}
          </button>
        )}
        <button
          type="button"
          disabled={deleteMutation.isPending}
          onClick={() => {
            if (!confirming) { setConfirming(true); return; }
            setErr(null);
            deleteMutation.mutate(undefined, {
              onSuccess: () => setConfirming(false),
              onError: (e) => { setErr(e instanceof Error ? e.message : 'Unlink failed'); setConfirming(false); },
            });
          }}
          onBlur={() => setConfirming(false)}
          style={{
            background: confirming ? '#e74c3c' : KC.surfaceHigh,
            color: confirming ? '#fff' : KC.onSurfaceVariant,
            border: `1px solid ${confirming ? '#e74c3c' : KC.borderMid}`,
            borderRadius: 4, padding: '4px 8px', fontSize: 11, fontFamily: 'monospace', cursor: 'pointer',
          }}
        >
          {confirming ? 'Confirm?' : 'Unlink'}
        </button>
      </div>
    </div>
  );
}

// ── NodeDetail (right floating panel) ────────────────────────────────────────
function NodeDetail({ node, onClose }: { node: TwinNode; onClose: () => void }) {
  const chat = useScopedChat({
    scopeKind: 'digital-twin-node',
    entityId: node.id,
    label: node.name,
  });
  const loadModel = useViewerStore((s) => s.loadModel);
  const setViewMode = useViewerStore((s) => s.setViewMode);
  const [loading3d, setLoading3d] = useState(false);
  const isCAD = node.properties.wp_type === 'cad_model';

  const handleView3D = useCallback(async () => {
    setLoading3d(true);
    try {
      const result = await getNodeModel(node.id);
      const manifest: ModelManifest = {
        parts: result.metadata.parts.map((p) => ({
          name: p.name,
          meshName: p.meshName ?? p.name,
          children: (p.children ?? []) as ModelManifest['parts'],
          boundingBox: p.boundingBox as PartTreeNode['boundingBox'],
        })),
        meshToNodeMap: {},
        materials: result.metadata.materials ?? [],
        stats: result.metadata.stats ?? { triangleCount: 0, fileSize: 0 },
      };
      const glbUrl = result.glb_url.startsWith('/v1/') ? `/api${result.glb_url}` : result.glb_url;
      loadModel(glbUrl, manifest);
      setViewMode('3d');
    } catch (err) {
      console.error('Failed to load 3D model:', err);
    } finally {
      setLoading3d(false);
    }
  }, [node.id, loadModel, setViewMode]);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div
        className="flex items-center justify-between px-3 flex-shrink-0"
        style={{ height: 36, borderBottom: `1px solid ${KC.border}` }}
      >
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined" style={{ fontSize: 14, color: KC.orange }}>
            {NODE_ICONS[node.type]}
          </span>
          <span className="font-mono text-xs truncate" style={{ color: KC.onSurface, maxWidth: 180 }}>
            {node.name}
          </span>
        </div>
        <button
          type="button"
          onClick={onClose}
          style={{ background: 'transparent', border: 'none', color: KC.onSurfaceVariant, cursor: 'pointer', padding: 4 }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.color = KC.onSurface; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.color = KC.onSurfaceVariant; }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 14 }}>close</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Status + meta */}
        <div className="px-3 py-2 flex-shrink-0" style={{ borderBottom: `1px solid ${KC.border}` }}>
          <StatusBadge status={node.status} />
          <div className="font-mono mt-1" style={{ fontSize: 10, color: KC.onSurfaceVariant }}>
            {node.domain} · {node.type} · {formatRelativeTime(node.updatedAt)}
          </div>
        </div>

        {/* View 3D */}
        {isCAD && (
          <div className="px-3 py-2 flex-shrink-0" style={{ borderBottom: `1px solid ${KC.border}` }}>
            <Button variant="primary" size="sm" onClick={handleView3D} disabled={loading3d} className="text-xs w-full">
              <span className="material-symbols-outlined" style={{ fontSize: 13, marginRight: 4, verticalAlign: 'middle' }}>view_in_ar</span>
              {loading3d ? 'Loading…' : 'View 3D Model'}
            </Button>
          </div>
        )}

        {/* Properties */}
        {Object.keys(node.properties).length > 0 && (
          <div className="px-3 py-2 flex-shrink-0" style={{ borderBottom: `1px solid ${KC.border}` }}>
            <div className="font-mono uppercase mb-1.5" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}>
              Properties
            </div>
            <table className="w-full" style={{ borderCollapse: 'collapse' }}>
              <tbody>
                {Object.entries(node.properties).map(([k, v]) => (
                  <tr key={k} style={{ borderBottom: '1px solid rgba(65,72,90,0.1)' }}>
                    <td className="py-1 pr-3 font-mono" style={{ fontSize: 11, color: KC.onSurfaceVariant, width: '40%' }}>{k}</td>
                    <td className="py-1 font-mono" style={{ fontSize: 11, color: KC.onSurface }}>{String(v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* File link */}
        <NodeLinkSection nodeId={node.id} />

        {/* Chat */}
        <div className="px-3 py-2 flex-1 min-h-0">
          <NodeChatPanel
            nodeId={node.id}
            nodeName={node.name}
            thread={chat.thread}
            messages={chat.messages}
            isTyping={chat.isTyping}
            onSendMessage={chat.sendMessage}
            onCreateThread={chat.createThread}
          />
        </div>
      </div>
    </div>
  );
}

// ── SceneDropdown ─────────────────────────────────────────────────────────────
function SceneDropdown({
  nodes,
  selectedId,
  onSelect,
}: {
  nodes: TwinNode[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    if (open) document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, [open]);

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 rounded px-3"
        style={{
          height: 28,
          background: 'rgba(30,31,38,0.85)',
          backdropFilter: 'blur(16px)',
          WebkitBackdropFilter: 'blur(16px)',
          border: `1px solid ${KC.border}`,
          fontSize: 11,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          color: KC.onSurfaceVariant,
          cursor: 'pointer',
          fontFamily: "'Roboto Mono', monospace",
        }}
      >
        <span className="material-symbols-outlined" style={{ fontSize: 14 }}>account_tree</span>
        SCENE
        <span style={{ fontSize: 10, marginLeft: 1 }}>▾</span>
      </button>

      {open && (
        <GlassPanel
          style={{
            position: 'absolute',
            top: 36,
            left: 0,
            width: 232,
            zIndex: 60,
            overflow: 'hidden',
            background: 'rgba(25,27,34,0.96)',
          }}
        >
          <div className="px-4 py-2.5" style={{ borderBottom: `1px solid ${KC.border}` }}>
            <span className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}>
              Twin Nodes · {nodes.length}
            </span>
          </div>
          <div className="py-1" style={{ maxHeight: 280, overflowY: 'auto' }}>
            {nodes.length === 0 ? (
              <div className="px-3 py-2 font-mono" style={{ fontSize: 12, color: KC.onSurfaceVariant }}>
                No nodes yet
              </div>
            ) : (
              nodes.map((n) => {
                const isActive = n.id === selectedId;
                return (
                  <button
                    key={n.id}
                    type="button"
                    onClick={() => { onSelect(n.id); setOpen(false); }}
                    className="flex items-center gap-2 w-full text-left"
                    style={{
                      padding: '6px 12px',
                      background: isActive ? KC.orangeFaint : 'transparent',
                      borderLeft: `2px solid ${isActive ? KC.orange : 'transparent'}`,
                      color: isActive ? KC.orange : KC.onSurfaceVariant,
                      fontSize: 12,
                      cursor: 'pointer',
                      border: 'none',
                      borderLeftWidth: 2,
                      borderLeftStyle: 'solid',
                      borderLeftColor: isActive ? KC.orange : 'transparent',
                      width: '100%',
                      fontFamily: 'Inter, sans-serif',
                    }}
                    onMouseEnter={(e) => {
                      if (!isActive) (e.currentTarget as HTMLButtonElement).style.background = KC.surfaceHigh;
                    }}
                    onMouseLeave={(e) => {
                      if (!isActive) (e.currentTarget as HTMLButtonElement).style.background = 'transparent';
                    }}
                  >
                    <span className="material-symbols-outlined flex-shrink-0" style={{ fontSize: 14 }}>
                      {NODE_ICONS[n.type]}
                    </span>
                    <span className="truncate">{n.name}</span>
                  </button>
                );
              })
            )}
          </div>
        </GlassPanel>
      )}
    </div>
  );
}

// ── TwinViewerPage ────────────────────────────────────────────────────────────
type ConversionPhase = 'idle' | 'uploading' | 'converting' | 'loading';

export function TwinViewerPage() {
  // ── state ──
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [conversionPhase, setConversionPhase] = useState<ConversionPhase>('idle');
  const [quality, setQuality] = useState('standard');
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── data ──
  const { data: nodes, isLoading } = useTwinNodes();
  const { data: selectedNode } = useTwinNode(selectedId ?? undefined);
  const { data: relationships } = useTwinRelationships();
  const items = nodes ?? [];
  const rels = relationships ?? [];

  // ── viewer store ──
  const viewMode = useViewerStore((s) => s.viewMode);
  const setViewMode = useViewerStore((s) => s.setViewMode);
  const manifest = useViewerStore((s) => s.manifest);
  const glbUrl = useViewerStore((s) => s.glbUrl);
  const selectPart = useViewerStore((s) => s.selectPart);
  const selectedMeshName = useViewerStore((s) => s.selectedMeshName);
  const loadModel = useViewerStore((s) => s.loadModel);

  const uploadMutation = useUploadAndConvert();

  useEffect(() => {
    if (!uploadMutation.isPending) {
      if (uploadMutation.isSuccess && conversionPhase === 'converting') {
        setConversionPhase('loading');
        const t = setTimeout(() => setConversionPhase('idle'), 800);
        return () => clearTimeout(t);
      }
      if (!uploadMutation.isSuccess) setConversionPhase('idle');
    }
  }, [uploadMutation.isPending, uploadMutation.isSuccess, conversionPhase]);

  const handleUpload = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) {
        setConversionPhase('uploading');
        const t = setTimeout(() => setConversionPhase('converting'), 1200);
        uploadMutation.mutate({ file, quality }, { onSettled: () => clearTimeout(t) });
      }
    },
    [uploadMutation, quality],
  );

  const handlePartClick = useCallback(
    (part: PartInfo) => { selectPart(part.meshName); },
    [selectPart],
  );

  const isGraphMode = viewMode === 'graph';

  // ── status bar label ──
  const statusLabel = isGraphMode ? 'GRAPH VIEW' : 'ORBIT MODE';
  const statusCenter = isGraphMode
    ? `${items.length} node${items.length !== 1 ? 's' : ''}`
    : 'X  0.0    Y  0.0    Z  0.0';

  return (
    /*
     * Full-bleed canvas: escapes AppLayout's p-6 (24px) padding by using
     * negative margins, then fills the remaining viewport height.
     */
    <div
      style={{
        position: 'relative',
        margin: -24,
        height: 'calc(100vh - 40px)', // 40px = h-10 topbar
        overflow: 'hidden',
        background: KC.surface,
        backgroundImage: 'radial-gradient(circle, rgba(154,154,170,0.18) 1px, transparent 1px)',
        backgroundSize: '32px 32px',
      }}
    >

      {/* ═══════════════════════════════════════════
          CANVAS — full-bleed content area
      ════════════════════════════════════════════ */}
      <div style={{ position: 'absolute', inset: 0 }}>
        {isGraphMode ? (
          isLoading ? (
            <div className="flex items-center justify-center h-full font-mono text-xs" style={{ color: KC.onSurfaceVariant }}>
              Loading twin graph…
            </div>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-3">
              <span className="material-symbols-outlined" style={{ fontSize: 40, color: KC.onSurfaceVariant, opacity: 0.4 }}>hub</span>
              <span className="font-mono text-xs" style={{ color: KC.onSurfaceVariant }}>Empty twin</span>
              <span className="font-mono" style={{ fontSize: 11, color: KC.onSurfaceVariant, opacity: 0.6 }}>
                Work products will appear here when agents run.
              </span>
            </div>
          ) : (
            <TwinGraphCanvas
              nodes={items}
              relationships={rels}
              selectedId={selectedId}
              onSelectNode={setSelectedId}
            />
          )
        ) : (
          /* 3D model mode */
          <>
            <R3FViewer onPartClick={handlePartClick} />
            {glbUrl && <ExplodedViewControls />}
          </>
        )}
      </div>

      {/* ═══════════════════════════════════════════
          GRAPH MODE: floating node list (left)
      ════════════════════════════════════════════ */}
      {isGraphMode && items.length > 0 && (
        <GlassPanel
          style={{
            position: 'absolute',
            top: 56,
            left: 16,
            bottom: 48,
            width: 260,
            zIndex: 40,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
        >
          <div
            className="flex items-center gap-2 px-3 flex-shrink-0"
            style={{ height: 36, borderBottom: `1px solid ${KC.border}` }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 14, color: KC.onSurfaceVariant }}>hub</span>
            <span className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}>
              Nodes
            </span>
            <span
              className="ml-auto font-mono rounded px-1.5"
              style={{ fontSize: 10, background: KC.surfaceHigh, color: KC.onSurfaceVariant }}
            >
              {items.length}
            </span>
          </div>
          <ul className="flex-1 overflow-y-auto" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {items.map((node) => {
              const active = node.id === selectedId;
              return (
                <li key={node.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(active ? null : node.id)}
                    className="flex w-full items-center gap-2 text-left"
                    style={{
                      height: 36,
                      padding: '0 12px',
                      background: active ? 'rgba(40,42,48,1)' : 'transparent',
                      borderLeft: 'none',
                      borderLeftWidth: 2,
                      borderLeftStyle: 'solid',
                      borderLeftColor: active ? KC.orange : 'transparent',
                      cursor: 'pointer',
                      border: 'none',
                      outline: 'none',
                      width: '100%',
                    }}
                    onMouseEnter={(e) => {
                      if (!active) (e.currentTarget as HTMLButtonElement).style.background = 'rgba(40,42,48,0.6)';
                    }}
                    onMouseLeave={(e) => {
                      if (!active) (e.currentTarget as HTMLButtonElement).style.background = 'transparent';
                    }}
                  >
                    <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: node.status === 'valid' || node.status === 'active' ? KC.green : KC.onSurfaceVariant, flexShrink: 0 }} />
                    <span className="material-symbols-outlined flex-shrink-0" style={{ fontSize: 14, color: active ? KC.orange : KC.onSurfaceVariant }}>
                      {NODE_ICONS[node.type]}
                    </span>
                    <span className="flex-1 truncate font-mono" style={{ fontSize: 12, color: active ? KC.onSurface : KC.onSurfaceVariant }}>
                      {node.name}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </GlassPanel>
      )}

      {/* ═══════════════════════════════════════════
          GRAPH MODE: floating node detail (right)
      ════════════════════════════════════════════ */}
      {isGraphMode && selectedNode && (
        <GlassPanel
          style={{
            position: 'absolute',
            top: 56,
            right: 72, // leave room for right toolbar (48px) + gap
            bottom: 48,
            width: 320,
            zIndex: 40,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
        >
          <NodeDetail node={selectedNode} onClose={() => setSelectedId(null)} />
        </GlassPanel>
      )}

      {/* ═══════════════════════════════════════════
          3D MODE: component tree (left panel)
      ════════════════════════════════════════════ */}
      {!isGraphMode && manifest && (
        <GlassPanel
          style={{
            position: 'absolute',
            top: 56,
            left: 16,
            bottom: 48,
            width: 240,
            zIndex: 40,
            overflow: 'hidden',
          }}
        >
          <ComponentTree />
        </GlassPanel>
      )}

      {/* ═══════════════════════════════════════════
          3D MODE: BOM annotation panel (right)
      ════════════════════════════════════════════ */}
      {!isGraphMode && selectedMeshName && manifest && (
        <GlassPanel
          style={{
            position: 'absolute',
            top: 56,
            right: 72,
            bottom: 48,
            width: 300,
            zIndex: 40,
            overflow: 'hidden',
          }}
        >
          <BomAnnotationPanel />
        </GlassPanel>
      )}

      {/* ═══════════════════════════════════════════
          TOP-LEFT: Scene dropdown + breadcrumb pill
      ════════════════════════════════════════════ */}
      <div
        className="flex items-center gap-2"
        style={{ position: 'absolute', top: 16, left: 16, zIndex: 50 }}
      >
        <SceneDropdown nodes={items} selectedId={selectedId} onSelect={setSelectedId} />

        {/* Breadcrumb pill */}
        <div
          className="flex items-center gap-1.5 rounded px-3"
          style={{
            height: 28,
            background: 'rgba(30,31,38,0.7)',
            backdropFilter: 'blur(16px)',
            WebkitBackdropFilter: 'blur(16px)',
            border: `1px solid ${KC.border}`,
          }}
        >
          <span style={{ fontSize: 12, color: KC.onSurfaceVariant }}>Digital Twin</span>
          {selectedNode && (
            <>
              <span style={{ fontSize: 11, color: 'rgba(154,154,170,0.4)' }}>›</span>
              <span style={{ fontSize: 12, color: KC.onSurface }}>{selectedNode.name}</span>
            </>
          )}
          <span
            className="ml-2 rounded px-1.5 font-mono"
            style={{
              fontSize: 9,
              fontWeight: 600,
              background: isGraphMode ? '#00a3e4' : KC.orange,
              color: isGraphMode ? '#fff' : KC.surface,
              letterSpacing: '0.04em',
            }}
          >
            {isGraphMode ? 'GRAPH' : '3D'}
          </span>
        </div>
      </div>

      {/* ═══════════════════════════════════════════
          TOP-RIGHT: MODEL|GRAPH toggle + utility buttons
      ════════════════════════════════════════════ */}
      <div
        className="flex items-center gap-2"
        style={{ position: 'absolute', top: 16, right: 64, zIndex: 50 }}
      >
        {/* Import */}
        <button
          type="button"
          onClick={() => setImportOpen((p) => !p)}
          className="flex items-center gap-1.5 rounded px-2"
          style={{
            height: 28,
            background: importOpen ? KC.orangeFaint : 'rgba(30,31,38,0.85)',
            backdropFilter: 'blur(16px)',
            border: `1px solid ${importOpen ? KC.orangeBorder : KC.borderMid}`,
            color: importOpen ? KC.orange : KC.onSurfaceVariant,
            fontSize: 11,
            cursor: 'pointer',
            letterSpacing: '0.06em',
            fontFamily: "'Roboto Mono', monospace",
          }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 14 }}>file_upload</span>
          IMPORT
        </button>

        {/* MODEL | GRAPH segmented toggle */}
        <div
          className="flex items-center rounded overflow-hidden"
          style={{
            background: 'rgba(25,27,34,0.9)',
            backdropFilter: 'blur(16px)',
            border: `1px solid ${KC.borderMid}`,
          }}
        >
          <button
            type="button"
            onClick={() => setViewMode('3d')}
            style={{
              padding: '0 12px',
              height: 28,
              fontSize: 11,
              fontFamily: "'Roboto Mono', monospace",
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              background: !isGraphMode ? KC.orangeFaint : 'transparent',
              color: !isGraphMode ? KC.orange : KC.onSurfaceVariant,
              border: 'none',
              cursor: 'pointer',
              transition: 'color 0.12s, background 0.12s',
            }}
          >
            MODEL
          </button>
          <div style={{ width: 1, height: 16, background: 'rgba(65,72,90,0.4)' }} />
          <button
            type="button"
            onClick={() => setViewMode('graph')}
            style={{
              padding: '0 12px',
              height: 28,
              fontSize: 11,
              fontFamily: "'Roboto Mono', monospace",
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              background: isGraphMode ? KC.orangeFaint : 'transparent',
              color: isGraphMode ? KC.orange : KC.onSurfaceVariant,
              border: 'none',
              cursor: 'pointer',
              transition: 'color 0.12s, background 0.12s',
            }}
          >
            GRAPH
          </button>
        </div>

        {/* Layers */}
        <button
          type="button"
          className="flex items-center justify-center rounded"
          style={{
            width: 32,
            height: 32,
            background: 'rgba(30,31,38,0.8)',
            backdropFilter: 'blur(16px)',
            border: `1px solid ${KC.border}`,
            color: KC.onSurfaceVariant,
            cursor: 'pointer',
          }}
          title="Layers"
        >
          <span className="material-symbols-outlined" style={{ fontSize: 18 }}>layers</span>
        </button>

        {/* Screenshot */}
        <button
          type="button"
          className="flex items-center justify-center rounded"
          style={{
            width: 32,
            height: 32,
            background: 'rgba(30,31,38,0.8)',
            backdropFilter: 'blur(16px)',
            border: `1px solid ${KC.border}`,
            color: KC.onSurfaceVariant,
            cursor: 'pointer',
          }}
          title="Screenshot"
        >
          <span className="material-symbols-outlined" style={{ fontSize: 18 }}>photo_camera</span>
        </button>
      </div>

      {/* ═══════════════════════════════════════════
          RIGHT: Vertical viewport toolbar
      ════════════════════════════════════════════ */}
      <GlassPanel
        style={{
          position: 'absolute',
          right: 16,
          top: '50%',
          transform: 'translateY(-50%)',
          zIndex: 50,
          padding: '4px 0',
          overflow: 'hidden',
        }}
      >
        <ToolBtn icon="hub" active={isGraphMode} title="Graph View" onClick={() => setViewMode('graph')} />
        <ToolBtn icon="account_tree" title="Tree View" />
        <ToolBtn icon="filter_alt" title="Filter" />
        <div style={{ height: 1, margin: '3px 8px', background: 'rgba(65,72,90,0.3)' }} />
        <ToolBtn icon="timeline" title="Timeline" />
        <ToolBtn icon="straighten" title="Measure" />
      </GlassPanel>

      {/* ═══════════════════════════════════════════
          IMPORT PANEL (slide-in under top bar)
      ════════════════════════════════════════════ */}
      {importOpen && (
        <GlassPanel
          style={{
            position: 'absolute',
            top: 52,
            right: 16,
            width: 320,
            zIndex: 50,
            overflow: 'hidden',
          }}
        >
          <div
            className="flex items-center justify-between px-3"
            style={{ height: 36, borderBottom: `1px solid ${KC.border}` }}
          >
            <span className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}>
              Import Work Product
            </span>
            <button
              type="button"
              onClick={() => setImportOpen(false)}
              style={{ background: 'transparent', border: 'none', color: KC.onSurfaceVariant, cursor: 'pointer', padding: 4 }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 14 }}>close</span>
            </button>
          </div>
          <div className="p-3">
            {/* Quality + upload row */}
            <div className="flex items-center gap-2 mb-3">
              <select
                value={quality}
                onChange={(e) => setQuality(e.target.value)}
                className="flex-1 font-mono rounded px-2 py-1 text-xs cursor-pointer"
                style={{
                  background: 'rgba(40,42,48,0.9)',
                  border: `1px solid ${KC.border}`,
                  color: KC.onSurfaceVariant,
                }}
              >
                <option value="preview">Preview</option>
                <option value="standard">Standard</option>
                <option value="fine">Fine</option>
              </select>
              {conversionPhase !== 'idle' ? (
                <div className="flex items-center gap-1.5 text-xs font-mono" style={{ color: KC.onSurfaceVariant }}>
                  <span className="material-symbols-outlined" style={{ fontSize: 13, color: KC.orange }}>sync</span>
                  {conversionPhase === 'uploading' ? 'Uploading…' : conversionPhase === 'converting' ? 'Converting…' : 'Loading…'}
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploadMutation.isPending}
                  className="flex items-center gap-1.5 rounded px-2 py-1 text-xs font-mono cursor-pointer"
                  style={{ background: KC.orange, color: KC.surface, border: 'none' }}
                >
                  <span className="material-symbols-outlined" style={{ fontSize: 13 }}>upload_file</span>
                  Upload STEP
                </button>
              )}
              {!glbUrl && (
                <button
                  type="button"
                  onClick={() => { loadModel(getMockGlbUrl(), getMockManifest()); setViewMode('3d'); setImportOpen(false); }}
                  className="rounded px-2 py-1 text-xs font-mono cursor-pointer"
                  style={{ background: 'transparent', border: `1px dashed ${KC.border}`, color: KC.onSurfaceVariant }}
                >
                  Demo
                </button>
              )}
            </div>

            {/* Drop zone */}
            <div
              className="flex flex-col items-center justify-center rounded cursor-pointer"
              style={{
                border: '2px dashed rgba(65,72,90,0.4)',
                padding: '20px 16px',
                textAlign: 'center',
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLDivElement).style.borderColor = 'rgba(230,126,34,0.4)';
                (e.currentTarget as HTMLDivElement).style.background = 'rgba(230,126,34,0.04)';
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLDivElement).style.borderColor = 'rgba(65,72,90,0.4)';
                (e.currentTarget as HTMLDivElement).style.background = 'transparent';
              }}
            >
              <span className="material-symbols-outlined mb-1.5" style={{ fontSize: 24, color: KC.onSurfaceVariant }}>file_upload</span>
              <p className="font-mono text-xs" style={{ color: KC.onSurface, marginBottom: 3 }}>
                Drag & drop or click to browse
              </p>
              <p className="font-mono" style={{ fontSize: 10, color: KC.onSurfaceVariant }}>
                .step .stp .iges .kicad_sch .kicad_pcb · max 100 MB
              </p>
            </div>
          </div>
          <input ref={fileInputRef} type="file" accept=".step,.stp,.iges,.igs" className="hidden" onChange={handleUpload} />
        </GlassPanel>
      )}

      {/* ═══════════════════════════════════════════
          BOTTOM-LEFT: Sessions button
      ════════════════════════════════════════════ */}
      <div style={{ position: 'absolute', bottom: 40, left: 16, zIndex: 50 }}>
        <Link to="/sessions" style={{ textDecoration: 'none' }}>
          <button
            type="button"
            className="flex items-center gap-1.5 rounded px-3"
            style={{
              height: 32,
              background: 'rgba(30,31,38,0.8)',
              backdropFilter: 'blur(16px)',
              border: `1px solid ${KC.border}`,
              fontSize: 10,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              color: KC.onSurfaceVariant,
              cursor: 'pointer',
              fontFamily: "'Roboto Mono', monospace",
            }}
            onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.color = KC.onSurface; }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.color = KC.onSurfaceVariant; }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 14 }}>schedule</span>
            Sessions
          </button>
        </Link>
      </div>

      {/* ═══════════════════════════════════════════
          STATUS BAR — 32px pinned to bottom
      ════════════════════════════════════════════ */}
      <footer
        className="flex items-center justify-between px-4"
        style={{
          position: 'absolute',
          bottom: 0,
          left: 0,
          right: 0,
          height: 32,
          zIndex: 50,
          background: KC.statusBar,
        }}
      >
        <span
          className="font-mono uppercase"
          style={{ fontSize: 11, letterSpacing: '0.08em', color: KC.onSurfaceVariant, width: 140 }}
        >
          {statusLabel}
        </span>

        <span className="font-mono" style={{ fontSize: 12, color: KC.onSurfaceVariant, letterSpacing: '0.05em' }}>
          {statusCenter}
        </span>

        <div className="flex items-center gap-2" style={{ width: 140, justifyContent: 'flex-end' }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#00a3e4', flexShrink: 0, display: 'inline-block' }} />
          <span className="font-mono" style={{ fontSize: 12, color: KC.onSurfaceVariant }}>Synced</span>
          <span className="font-mono" style={{ fontSize: 11, color: 'rgba(154,154,170,0.55)' }}>live</span>
        </div>
      </footer>

    </div>
  );
}
