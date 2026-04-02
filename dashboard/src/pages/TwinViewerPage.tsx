/**
 * Digital Twin page — Kinetic Console design (MET-243)
 *
 * Layout:
 *   Header (40px) — breadcrumb + filter pills
 *   Middle row:
 *     Left (flex-1): SVG graph canvas
 *     Right (288px): node detail panel — tabs: Overview | History | Linked
 *   BOM strip (28px) — full-width status bar
 *
 * For 3D viewing, the existing R3F viewer is accessible via the "3D" view toggle
 * in the header.
 */

import { useState, useRef, useCallback, useMemo, useEffect } from 'react';
import { Box, GitBranch, Upload, ChevronDown } from 'lucide-react';
import { EmptyState } from '../components/ui/EmptyState';
import { formatRelativeTime } from '../utils/format-time';
import { useTwinNodes, useTwinRelationships, useNodeVersionHistory } from '../hooks/use-twin';
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
import type { TwinNode, TwinRelationship } from '../types/twin';
import type { ModelManifest, PartInfo, PartTreeNode } from '../types/viewer';

// ---------------------------------------------------------------------------
// Color map
// ---------------------------------------------------------------------------

const DOMAIN_COLORS: Record<string, string> = {
  mechanical: '#e67e22',
  electronics: '#00a3e4',
  firmware: '#3dd68c',
  requirements: '#86cfff',
  simulation: '#f59e0b',
  default: '#9a9aaa',
};

const NODE_TYPE_COLORS: Record<string, string> = {
  work_product: '#e67e22',
  constraint: '#f59e0b',
  version: '#86cfff',
  relationship: '#9a9aaa',
};

function domainColor(node: TwinNode): string {
  return DOMAIN_COLORS[node.domain] ?? NODE_TYPE_COLORS[node.type] ?? (DOMAIN_COLORS['default'] as string);
}

// ---------------------------------------------------------------------------
// Graph layout helpers
// ---------------------------------------------------------------------------

interface NodePosition {
  x: number;
  y: number;
}

/** Simple grid layout — groups by domain, rows by type. */
function computeLayout(nodes: TwinNode[], width: number, height: number): Map<string, NodePosition> {
  const positions = new Map<string, NodePosition>();
  const groups: Record<string, TwinNode[]> = {};
  for (const n of nodes) {
    const key = n.domain || n.type;
    (groups[key] ??= []).push(n);
  }
  const groupKeys = Object.keys(groups);
  const cols = Math.max(1, groupKeys.length);
  const colW = width / cols;
  const padX = 48;
  const padY = 48;

  groupKeys.forEach((gk, gi) => {
    const groupNodes = groups[gk] ?? [];
    const rows = groupNodes.length;
    const rowH = Math.min(64, (height - padY * 2) / Math.max(rows, 1));
    groupNodes.forEach((n, ri) => {
      positions.set(n.id, {
        x: padX + gi * colW + colW / 2,
        y: padY + ri * rowH + rowH / 2,
      });
    });
  });
  return positions;
}

// ---------------------------------------------------------------------------
// SVG Graph Canvas
// ---------------------------------------------------------------------------

interface GraphCanvasProps {
  nodes: TwinNode[];
  relationships: TwinRelationship[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

function GraphCanvas({ nodes, relationships, selectedId, onSelect }: GraphCanvasProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [viewBox, setViewBox] = useState({ x: 0, y: 0, scale: 1 });
  const [dragging, setDragging] = useState(false);
  const dragStart = useRef<{ mx: number; my: number; vx: number; vy: number } | null>(null);
  const [dims, setDims] = useState({ w: 800, h: 500 });

  useEffect(() => {
    const el = svgRef.current?.parentElement;
    if (!el) return;
    const obs = new ResizeObserver((entries) => {
      const r = entries[0]?.contentRect;
      if (r) setDims({ w: r.width, h: r.height });
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  const positions = useMemo(
    () => computeLayout(nodes, dims.w, dims.h),
    [nodes, dims],
  );

  // Pan handlers
  const handleMouseDown = useCallback((e: React.MouseEvent<SVGSVGElement>) => {
    if ((e.target as SVGElement).closest('[data-node]')) return;
    setDragging(true);
    dragStart.current = { mx: e.clientX, my: e.clientY, vx: viewBox.x, vy: viewBox.y };
  }, [viewBox]);

  const handleMouseMove = useCallback((e: React.MouseEvent<SVGSVGElement>) => {
    if (!dragging || !dragStart.current) return;
    const dx = (e.clientX - dragStart.current.mx) / viewBox.scale;
    const dy = (e.clientY - dragStart.current.my) / viewBox.scale;
    setViewBox(v => ({ ...v, x: dragStart.current!.vx - dx, y: dragStart.current!.vy - dy }));
  }, [dragging, viewBox.scale]);

  const handleMouseUp = useCallback(() => {
    setDragging(false);
    dragStart.current = null;
  }, []);

  const handleWheel = useCallback((e: React.WheelEvent<SVGSVGElement>) => {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    setViewBox(v => ({ ...v, scale: Math.max(0.3, Math.min(3, v.scale * factor)) }));
  }, []);

  const fitToView = useCallback(() => {
    setViewBox({ x: 0, y: 0, scale: 1 });
  }, []);

  const NODE_R = 22;

  if (nodes.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <EmptyState
          title="Empty twin graph"
          description="Import work products or run an agent to populate the Digital Twin."
        />
      </div>
    );
  }

  const transform = `scale(${viewBox.scale}) translate(${-viewBox.x}, ${-viewBox.y})`;

  return (
    <div className="relative flex-1 overflow-hidden" style={{ background: '#0a0b0f' }}>
      {/* Dot grid */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          backgroundImage: 'radial-gradient(circle, rgba(154,154,170,0.18) 1px, transparent 1px)',
          backgroundSize: '32px 32px',
        }}
      />

      {/* Controls overlay */}
      <div className="absolute top-3 right-3 z-10 flex flex-col gap-1.5">
        <button
          onClick={fitToView}
          className="flex h-7 w-7 items-center justify-center rounded text-xs"
          style={{ background: 'rgba(30,31,38,0.88)', color: '#9a9aaa', border: '1px solid rgba(65,72,90,0.25)' }}
          title="Fit to view"
        >
          <span className="material-symbols-outlined" style={{ fontSize: 14 }}>fit_screen</span>
        </button>
        <button
          onClick={() => setViewBox(v => ({ ...v, scale: Math.min(3, v.scale * 1.2) }))}
          className="flex h-7 w-7 items-center justify-center rounded text-xs"
          style={{ background: 'rgba(30,31,38,0.88)', color: '#9a9aaa', border: '1px solid rgba(65,72,90,0.25)' }}
          title="Zoom in"
        >
          <span className="material-symbols-outlined" style={{ fontSize: 14 }}>add</span>
        </button>
        <button
          onClick={() => setViewBox(v => ({ ...v, scale: Math.max(0.3, v.scale * 0.8) }))}
          className="flex h-7 w-7 items-center justify-center rounded text-xs"
          style={{ background: 'rgba(30,31,38,0.88)', color: '#9a9aaa', border: '1px solid rgba(65,72,90,0.25)' }}
          title="Zoom out"
        >
          <span className="material-symbols-outlined" style={{ fontSize: 14 }}>remove</span>
        </button>
      </div>

      {/* Badge */}
      <div
        className="absolute bottom-3 left-3 z-10 flex items-center gap-2 rounded px-3 py-1"
        style={{ background: 'rgba(30,31,38,0.85)', backdropFilter: 'blur(8px)', fontSize: 11, color: '#9a9aaa', fontFamily: 'Roboto Mono, monospace' }}
      >
        <span style={{ color: '#e2e2eb' }}>{nodes.length}</span> nodes ·{' '}
        <span style={{ color: '#e2e2eb' }}>{relationships.length}</span> edges
      </div>

      <svg
        ref={svgRef}
        className="absolute inset-0 w-full h-full"
        style={{ cursor: dragging ? 'grabbing' : 'grab' }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onWheel={handleWheel}
      >
        <defs>
          <marker id="arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
            <path d="M0,0 L0,6 L6,3 z" fill="rgba(65,72,90,0.7)" />
          </marker>
          {/* Selection pulse ring */}
          <filter id="glow">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
        </defs>

        <g transform={transform}>
          {/* Edges */}
          {relationships.map((rel) => {
            const src = positions.get(rel.sourceId);
            const tgt = positions.get(rel.targetId);
            if (!src || !tgt) return null;
            const dx = tgt.x - src.x;
            const dy = tgt.y - src.y;
            const len = Math.sqrt(dx * dx + dy * dy) || 1;
            const ex = tgt.x - (dx / len) * (NODE_R + 6);
            const ey = tgt.y - (dy / len) * (NODE_R + 6);
            const mx = (src.x + tgt.x) / 2;
            const my = (src.y + tgt.y) / 2;
            return (
              <g key={rel.id}>
                <line
                  x1={src.x} y1={src.y} x2={ex} y2={ey}
                  stroke="rgba(65,72,90,0.5)" strokeWidth={1}
                  markerEnd="url(#arrow)"
                />
                <text
                  x={mx} y={my - 5}
                  textAnchor="middle"
                  style={{ fontSize: 9, fill: '#9a9aaa', fontFamily: 'Roboto Mono, monospace', pointerEvents: 'none' }}
                >
                  {rel.type.replace(/_/g, ' ')}
                </text>
              </g>
            );
          })}

          {/* Nodes */}
          {nodes.map((node) => {
            const pos = positions.get(node.id);
            if (!pos) return null;
            const color = domainColor(node);
            const isSelected = node.id === selectedId;

            return (
              <g
                key={node.id}
                data-node="true"
                onClick={() => onSelect(node.id)}
                style={{ cursor: 'pointer' }}
                transform={`translate(${pos.x}, ${pos.y})`}
              >
                {/* Selection ring */}
                {isSelected && (
                  <circle r={NODE_R + 6} fill="none" stroke={color} strokeWidth={1.5} opacity={0.5} strokeDasharray="5 3" />
                )}
                {/* Node circle */}
                <circle
                  r={NODE_R}
                  fill={isSelected ? `${color}22` : 'rgba(30,31,38,0.9)'}
                  stroke={isSelected ? color : 'rgba(65,72,90,0.6)'}
                  strokeWidth={isSelected ? 2 : 1}
                  filter={isSelected ? 'url(#glow)' : undefined}
                />
                {/* Domain color dot */}
                <circle r={5} cx={NODE_R - 6} cy={-(NODE_R - 6)} fill={color} />
                {/* Node label */}
                <text
                  y={4}
                  textAnchor="middle"
                  style={{
                    fontSize: 9,
                    fill: isSelected ? '#e2e2eb' : '#9a9aaa',
                    fontFamily: 'Inter, sans-serif',
                    fontWeight: isSelected ? 500 : 400,
                    pointerEvents: 'none',
                    userSelect: 'none',
                  }}
                >
                  {node.name.length > 12 ? node.name.slice(0, 11) + '…' : node.name}
                </text>
                {/* Domain label below */}
                <text
                  y={16}
                  textAnchor="middle"
                  style={{
                    fontSize: 8,
                    fill: color,
                    fontFamily: 'Roboto Mono, monospace',
                    pointerEvents: 'none',
                    userSelect: 'none',
                    opacity: 0.75,
                  }}
                >
                  {node.domain.toUpperCase().slice(0, 6)}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Version History tab
// ---------------------------------------------------------------------------

function VersionHistoryTab({ nodeId }: { nodeId: string }) {
  const { data: history, isLoading } = useNodeVersionHistory(nodeId);

  if (isLoading) {
    return <div style={{ fontSize: 12, color: '#9a9aaa', padding: '12px 0' }}>Loading history…</div>;
  }

  if (!history || history.total === 0) {
    return (
      <div style={{ fontSize: 12, color: '#9a9aaa', padding: '12px 0' }}>
        No revision history yet.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {[...history.revisions].reverse().map((rev, idx) => {
        const isCurrent = idx === 0;
        return (
          <div
            key={rev.revision}
            className="flex items-start gap-3 py-2"
            style={{ borderBottom: '1px solid rgba(65,72,90,0.2)' }}
          >
            {/* Timeline dot */}
            <div className="mt-1 flex flex-col items-center gap-1 flex-shrink-0">
              <div
                style={{
                  width: 8, height: 8, borderRadius: '50%',
                  background: isCurrent ? '#e67e22' : 'rgba(65,72,90,0.6)',
                  flexShrink: 0,
                }}
              />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-0.5">
                <span style={{ fontSize: 10, fontFamily: 'Roboto Mono, monospace', color: '#e67e22', letterSpacing: '0.06em' }}>
                  v{rev.revision}
                </span>
                {isCurrent && (
                  <span
                    className="rounded px-1.5"
                    style={{ fontSize: 9, fontWeight: 600, background: 'rgba(230,126,34,0.15)', color: '#e67e22', letterSpacing: '0.04em' }}
                  >
                    CURRENT
                  </span>
                )}
                <span style={{ fontSize: 10, color: '#9a9aaa', marginLeft: 'auto' }}>
                  {formatRelativeTime(rev.created_at)}
                </span>
              </div>
              <p style={{ fontSize: 11, color: '#e2e2eb', margin: 0 }}>
                {rev.change_description}
              </p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Overview tab
// ---------------------------------------------------------------------------

function OverviewTab({ node }: { node: TwinNode }) {
  const importantKeys = ['wp_type', 'format', 'domain', 'file_path', 'content_hash'];
  const entries = Object.entries(node.properties).filter(([k]) => importantKeys.includes(k));
  const rest = Object.entries(node.properties).filter(([k]) => !importantKeys.includes(k));

  return (
    <div className="space-y-4">
      {/* Key properties */}
      <div className="grid grid-cols-2 gap-3" style={{ fontSize: 12 }}>
        {entries.map(([key, value]) => (
          <div key={key}>
            <p style={{ color: '#9a9aaa', marginBottom: 2, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              {key.replace(/_/g, ' ')}
            </p>
            <p style={{ color: '#e2e2eb', fontFamily: key === 'content_hash' || key === 'file_path' ? 'Roboto Mono, monospace' : 'inherit', fontSize: 11 }}>
              {String(value).length > 22 ? String(value).slice(0, 21) + '…' : String(value)}
            </p>
          </div>
        ))}
      </div>

      {/* Additional properties */}
      {rest.length > 0 && (
        <div>
          <p style={{ fontSize: 10, color: '#9a9aaa', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8 }}>
            Metadata
          </p>
          <div className="space-y-1">
            {rest.slice(0, 8).map(([key, value]) => (
              <div key={key} className="flex items-start gap-2" style={{ fontSize: 11 }}>
                <span style={{ color: '#9a9aaa', width: 88, flexShrink: 0 }}>{key.replace(/_/g, ' ')}</span>
                <span style={{ color: '#e2e2eb', fontFamily: 'Roboto Mono, monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {String(value)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Node Detail Panel
// ---------------------------------------------------------------------------

type PanelTab = 'overview' | 'history' | 'linked';

function NodeDetailPanel({ node }: { node: TwinNode }) {
  const [tab, setTab] = useState<PanelTab>('overview');
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
      const glbUrl = result.glb_url.startsWith('/v1/')
        ? `/api${result.glb_url}`
        : result.glb_url;
      loadModel(glbUrl, manifest);
      setViewMode('3d');
    } catch (err) {
      console.error('Failed to load 3D model:', err);
    } finally {
      setLoading3d(false);
    }
  }, [node.id, loadModel, setViewMode]);

  const color = domainColor(node);

  return (
    <div className="flex h-full flex-col" style={{ background: '#1e1f26' }}>
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 flex-shrink-0" style={{ borderBottom: '1px solid rgba(65,72,90,0.25)' }}>
        <div style={{ width: 8, height: 8, borderRadius: '50%', background: color, flexShrink: 0 }} />
        <span className="flex-1 truncate" style={{ fontSize: 13, fontWeight: 500, color: '#e2e2eb' }}>
          {node.name}
        </span>
        <button
          className="flex items-center gap-1 hover:opacity-80 transition-opacity flex-shrink-0"
          style={{ fontSize: 11, color: '#e67e22' }}
          onClick={() => {/* toggle chat */}}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 13 }}>auto_awesome</span>
          Ask AI
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-0 px-4 flex-shrink-0" style={{ borderBottom: '1px solid rgba(65,72,90,0.25)' }}>
        {(['overview', 'history', 'linked'] as PanelTab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className="capitalize mr-4 hover:opacity-100 transition-opacity"
            style={{
              fontSize: 12,
              color: tab === t ? '#e2e2eb' : '#9a9aaa',
              paddingBottom: 8,
              paddingTop: 8,
              borderBottom: tab === t ? '2px solid #e67e22' : '2px solid transparent',
            }}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto px-4 py-3">
        {tab === 'overview' && <OverviewTab node={node} />}
        {tab === 'history' && <VersionHistoryTab nodeId={node.id} />}
        {tab === 'linked' && (
          <div style={{ fontSize: 12, color: '#9a9aaa' }}>
            File links and source connections will appear here.
          </div>
        )}
      </div>

      {/* 3D View button (CAD only) */}
      {isCAD && (
        <div className="flex-shrink-0 px-4 py-3" style={{ borderTop: '1px solid rgba(65,72,90,0.25)' }}>
          <button
            onClick={handleView3D}
            disabled={loading3d}
            className="flex w-full items-center justify-center gap-1.5 rounded py-2 transition-opacity hover:opacity-90 disabled:opacity-50"
            style={{ fontSize: 12, background: 'rgba(230,126,34,0.15)', color: '#e67e22', border: '1px solid rgba(230,126,34,0.3)' }}
          >
            <Box size={13} />
            {loading3d ? 'Converting…' : 'View 3D Model'}
          </button>
        </div>
      )}

      {/* Chat */}
      <div className="flex-shrink-0 border-t px-4 py-3" style={{ borderColor: 'rgba(65,72,90,0.25)' }}>
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
  );
}

// ---------------------------------------------------------------------------
// Constraint Violations panel (stub — real data from constraint engine API)
// ---------------------------------------------------------------------------

const MOCK_VIOLATIONS = [
  { id: 'v1', severity: 'HIGH', description: 'Thermal expansion > 0.05mm on Actuator Housing (REQ-042)' },
  { id: 'v2', severity: 'MEDIUM', description: 'Power budget headroom < 10% — electronics domain' },
];

function ConstraintViolationsPanel() {
  if (MOCK_VIOLATIONS.length === 0) {
    return (
      <div className="flex items-center gap-1.5 px-4 py-3" style={{ fontSize: 11, color: '#3dd68c' }}>
        <span className="material-symbols-outlined" style={{ fontSize: 14 }}>check_circle</span>
        All constraints passing
      </div>
    );
  }

  return (
    <div className="overflow-y-auto" style={{ maxHeight: 140 }}>
      <p style={{ fontSize: 10, letterSpacing: '0.08em', textTransform: 'uppercase', color: '#9a9aaa', padding: '8px 16px 4px', fontWeight: 500 }}>
        Constraint Violations
      </p>
      {MOCK_VIOLATIONS.map((v) => (
        <div
          key={v.id}
          className="flex items-start gap-2 px-4 py-2"
          style={{ borderTop: '1px solid rgba(65,72,90,0.2)' }}
        >
          <span
            className="flex-shrink-0 rounded px-1.5 py-0.5"
            style={{
              fontSize: 9,
              fontWeight: 700,
              letterSpacing: '0.05em',
              background: v.severity === 'HIGH' ? 'rgba(255,180,171,0.15)' : 'rgba(245,158,11,0.15)',
              color: v.severity === 'HIGH' ? '#ffb4ab' : '#f59e0b',
            }}
          >
            {v.severity}
          </span>
          <p style={{ fontSize: 11, color: '#9a9aaa', margin: 0, lineHeight: 1.4 }}>
            {v.description}
          </p>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Node list (left sidebar alternative for when canvas is too small)
// ---------------------------------------------------------------------------

function NodeList({ nodes, selectedId, onSelect }: { nodes: TwinNode[]; selectedId: string | null; onSelect: (id: string) => void }) {
  return (
    <div className="overflow-y-auto" style={{ background: '#191b22', borderRight: '1px solid rgba(65,72,90,0.2)', width: 196, flexShrink: 0 }}>
      <p style={{ fontSize: 10, letterSpacing: '0.08em', textTransform: 'uppercase', color: '#9a9aaa', padding: '10px 12px 6px', fontWeight: 500 }}>
        Components
      </p>
      {nodes.map((node) => {
        const color = domainColor(node);
        const isSelected = node.id === selectedId;
        return (
          <button
            key={node.id}
            onClick={() => onSelect(node.id)}
            className="flex w-full items-center gap-2 text-left transition-colors"
            style={{
              padding: '7px 12px',
              background: isSelected ? '#1e1f26' : 'transparent',
              borderLeft: isSelected ? `2px solid ${color}` : '2px solid transparent',
            }}
          >
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: color, flexShrink: 0 }} />
            <span
              className="flex-1 truncate"
              style={{ fontSize: 12, color: isSelected ? '#e2e2eb' : '#9a9aaa' }}
            >
              {node.name}
            </span>
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// BOM strip (bottom status bar)
// ---------------------------------------------------------------------------

function BomStrip({ nodeCount }: { nodeCount: number }) {
  return (
    <div
      className="flex items-center gap-6 px-4 flex-shrink-0"
      style={{
        height: 28,
        background: '#191b22',
        fontFamily: 'Roboto Mono, monospace',
        fontSize: 10,
        color: '#9a9aaa',
        borderTop: '1px solid rgba(65,72,90,0.2)',
      }}
    >
      <span className="flex items-center gap-1.5">
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#00a3e4', display: 'inline-block', flexShrink: 0 }} />
        L1 Digital Thread
      </span>
      <span>{nodeCount} components</span>
      <span>Est. cost: —</span>
      <span style={{ color: '#f59e0b' }}>{MOCK_VIOLATIONS.length > 0 ? `${MOCK_VIOLATIONS.length} risk items` : 'No risks'}</span>
      <span className="ml-auto">Gate: Design Review</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 3D Viewer toolbar (carried over from original)
// ---------------------------------------------------------------------------

function ViewerToolbar3D() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadMutation = useUploadAndConvert();
  const loadModel = useViewerStore((s) => s.loadModel);
  const glbUrl = useViewerStore((s) => s.glbUrl);
  const [quality, setQuality] = useState('standard');

  const handleUpload = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) uploadMutation.mutate({ file, quality });
    },
    [uploadMutation, quality],
  );

  const handleLoadMock = useCallback(() => {
    loadModel(getMockGlbUrl(), getMockManifest());
  }, [loadModel]);

  return (
    <div className="flex items-center gap-2">
      <div className="relative">
        <select
          value={quality}
          onChange={(e) => setQuality(e.target.value)}
          className="appearance-none rounded px-3 py-1 pr-6 text-xs"
          style={{ background: 'rgba(30,31,38,0.88)', border: '1px solid rgba(65,72,90,0.4)', color: '#9a9aaa', fontSize: 11 }}
        >
          <option value="preview">Preview</option>
          <option value="standard">Standard</option>
          <option value="fine">Fine</option>
        </select>
        <ChevronDown size={10} className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2" style={{ color: '#9a9aaa' }} />
      </div>
      <button
        onClick={() => fileInputRef.current?.click()}
        disabled={uploadMutation.isPending}
        className="flex items-center gap-1.5 rounded px-3 py-1 text-xs transition-opacity hover:opacity-80 disabled:opacity-50"
        style={{ background: 'rgba(30,31,38,0.88)', border: '1px solid rgba(65,72,90,0.4)', color: '#9a9aaa', fontSize: 11 }}
      >
        <Upload size={12} />
        {uploadMutation.isPending ? 'Converting…' : 'Upload STEP'}
      </button>
      <input ref={fileInputRef} type="file" accept=".step,.stp,.iges,.igs" className="hidden" onChange={handleUpload} />
      {!glbUrl && (
        <button
          onClick={handleLoadMock}
          className="rounded px-3 py-1 text-xs transition-opacity hover:opacity-80"
          style={{ border: '1px dashed rgba(65,72,90,0.5)', color: '#9a9aaa', fontSize: 11 }}
        >
          Load Demo
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function TwinViewerPage() {
  const { data: nodes, isLoading } = useTwinNodes();
  const { data: relationships } = useTwinRelationships();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'graph' | '3d'>('graph');
  const [domainFilter, setDomainFilter] = useState<string | null>(null);

  // 3D viewer state
  const manifest = useViewerStore((s) => s.manifest);
  const glbUrl = useViewerStore((s) => s.glbUrl);
  const selectPart = useViewerStore((s) => s.selectPart);
  const selectedMeshName = useViewerStore((s) => s.selectedMeshName);
  const setViewerMode = useViewerStore((s) => s.setViewMode);

  // Sync 3D viewer mode when switching tabs
  useEffect(() => {
    if (viewMode === '3d') setViewerMode('3d');
    else setViewerMode('graph');
  }, [viewMode, setViewerMode]);

  const handlePartClick = useCallback((part: PartInfo) => {
    selectPart(part.meshName);
  }, [selectPart]);

  const items = nodes ?? [];
  const rels = relationships ?? [];

  // Domain filter
  const domains = useMemo(() => {
    const s = new Set(items.map((n) => n.domain).filter(Boolean));
    return Array.from(s);
  }, [items]);

  const filteredNodes = useMemo(
    () => (domainFilter ? items.filter((n) => n.domain === domainFilter) : items),
    [items, domainFilter],
  );

  const selectedNode = items.find((n) => n.id === selectedId) ?? null;

  return (
    <div
      className="flex flex-col"
      style={{ height: 'calc(100vh - 4rem)', background: '#111319', color: '#e2e2eb' }}
    >
      {/* ── Header ── */}
      <div
        className="flex items-center justify-between px-4 flex-shrink-0"
        style={{ height: 40, background: '#1e1f26', borderBottom: '1px solid rgba(65,72,90,0.25)' }}
      >
        {/* Left: breadcrumb + badge */}
        <div className="flex items-center gap-2">
          <span style={{ fontSize: 13, color: '#e2e2eb' }}>
            Digital Twin /&nbsp;
            <span style={{ color: '#9a9aaa' }}>Design Graph</span>
          </span>
          <span
            className="rounded-full px-2 py-0.5"
            style={{ fontSize: 10, background: '#00a3e4', color: '#fff', fontWeight: 500 }}
          >
            L1 Digital Thread
          </span>
        </div>

        {/* Right: view mode toggle + filter pills */}
        <div className="flex items-center gap-3">
          {/* Domain filter pills */}
          <div className="flex items-center gap-1">
            <button
              onClick={() => setDomainFilter(null)}
              className="rounded-full px-3 py-1 text-xs transition-colors"
              style={{
                fontSize: 11,
                background: domainFilter === null ? 'rgba(230,126,34,0.15)' : 'rgba(154,154,170,0.1)',
                color: domainFilter === null ? '#e67e22' : '#9a9aaa',
              }}
            >
              All
            </button>
            {domains.slice(0, 4).map((d) => (
              <button
                key={d}
                onClick={() => setDomainFilter(d === domainFilter ? null : d)}
                className="rounded-full px-3 py-1 text-xs transition-colors"
                style={{
                  fontSize: 11,
                  background: domainFilter === d ? `${DOMAIN_COLORS[d] ?? '#9a9aaa'}22` : 'rgba(154,154,170,0.08)',
                  color: domainFilter === d ? (DOMAIN_COLORS[d] ?? '#9a9aaa') : '#9a9aaa',
                }}
              >
                {d.charAt(0).toUpperCase() + d.slice(1)}
              </button>
            ))}
          </div>

          {/* View toggle */}
          <div className="flex rounded overflow-hidden" style={{ border: '1px solid rgba(65,72,90,0.35)' }}>
            <button
              onClick={() => setViewMode('graph')}
              className="flex items-center gap-1.5 px-3 py-1 text-xs transition-colors"
              style={{
                fontSize: 11,
                background: viewMode === 'graph' ? 'rgba(230,126,34,0.15)' : 'transparent',
                color: viewMode === 'graph' ? '#e67e22' : '#9a9aaa',
              }}
            >
              <GitBranch size={12} />
              Graph
            </button>
            <button
              onClick={() => setViewMode('3d')}
              className="flex items-center gap-1.5 px-3 py-1 text-xs transition-colors"
              style={{
                fontSize: 11,
                borderLeft: '1px solid rgba(65,72,90,0.35)',
                background: viewMode === '3d' ? 'rgba(0,163,228,0.12)' : 'transparent',
                color: viewMode === '3d' ? '#00a3e4' : '#9a9aaa',
              }}
            >
              <Box size={12} />
              3D
            </button>
          </div>

          {viewMode === '3d' && <ViewerToolbar3D />}
        </div>
      </div>

      {/* ── Content ── */}
      {viewMode === 'graph' ? (
        <div className="flex flex-1 overflow-hidden">
          {/* Node list sidebar */}
          {!isLoading && (
            <NodeList
              nodes={filteredNodes}
              selectedId={selectedId}
              onSelect={setSelectedId}
            />
          )}

          {/* Graph canvas */}
          <div className="flex flex-1 flex-col overflow-hidden">
            {isLoading ? (
              <div className="flex flex-1 items-center justify-center" style={{ color: '#9a9aaa', fontSize: 13 }}>
                Loading twin graph…
              </div>
            ) : (
              <GraphCanvas
                nodes={filteredNodes}
                relationships={rels}
                selectedId={selectedId}
                onSelect={setSelectedId}
              />
            )}

            {/* Constraint violations strip */}
            <div style={{ background: '#1e1f26', borderTop: '1px solid rgba(65,72,90,0.2)' }}>
              <ConstraintViolationsPanel />
            </div>
          </div>

          {/* Right panel */}
          <div className="flex-shrink-0 overflow-hidden" style={{ width: 288, borderLeft: '1px solid rgba(65,72,90,0.2)' }}>
            {selectedNode ? (
              <NodeDetailPanel node={selectedNode} />
            ) : (
              <div className="flex h-full items-center justify-center" style={{ color: '#9a9aaa', fontSize: 12 }}>
                Select a node to view details
              </div>
            )}
          </div>
        </div>
      ) : (
        /* 3D view — existing R3F viewer */
        <div className="flex flex-1 overflow-hidden">
          {manifest && (
            <div className="flex-shrink-0" style={{ width: 192, borderRight: '1px solid rgba(65,72,90,0.2)' }}>
              <ComponentTree />
            </div>
          )}
          <div className="relative flex-1">
            <R3FViewer onPartClick={handlePartClick} />
            {glbUrl && <ExplodedViewControls />}
          </div>
          {selectedMeshName && manifest && (
            <div className="flex-shrink-0" style={{ width: 288, borderLeft: '1px solid rgba(65,72,90,0.2)' }}>
              <BomAnnotationPanel />
            </div>
          )}
        </div>
      )}

      {/* ── BOM strip ── */}
      <BomStrip nodeCount={items.length} />
    </div>
  );
}
