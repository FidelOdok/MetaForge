/**
 * TwinGraphCanvas — interactive Digital Thread graph using @xyflow/react.
 *
 * Renders work-product nodes and typed relationship edges fetched from the
 * Twin API. Nodes are laid out in a simple left-to-right grid; users can
 * pan, zoom, and click to select nodes.
 */

import { useCallback, useEffect, useMemo, useRef } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  MiniMap,
  type Node,
  type Edge,
  type NodeMouseHandler,
  type ReactFlowInstance,
  Position,
  MarkerType,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { TwinEndpoint, TwinNode, TwinRelationship } from '../../types/twin';
import { groupOf, type LinkGroup } from '../../lib/graphLinks';
import { WP_TYPE_ICONS } from '../../utils/wp-icons';

// ── KC tokens ─────────────────────────────────────────────────────────────────
const KC = {
  surface: 'var(--mf-c-111319)',
  surfaceContainer: 'var(--mf-r-30-31-38-0p92)',
  surfaceHigh: 'var(--mf-c-282a30)',
  border: 'var(--mf-r-65-72-90-0p35)',
  onSurface: 'var(--mf-c-e2e2eb)',
  onSurfaceVariant: 'var(--mf-c-9a9aaa)',
  orange: '#ff5a0a',
  green: 'var(--mf-c-3dd68c)',
  teal: 'var(--mf-c-86cfff)',
  amber: 'var(--mf-c-f5a623)',
  red: 'var(--mf-c-e74c3c)',
} as const;

// ── Domain → accent colour ────────────────────────────────────────────────────
const DOMAIN_COLOR: Record<string, string> = {
  mechanical: KC.teal,
  electronics: KC.amber,
  firmware: KC.green,
  simulation: 'var(--mf-c-a78bfa)',
  compliance: 'var(--mf-c-f472b6)',
  supply_chain: 'var(--mf-c-34d399)',
  industrial_design: 'var(--mf-c-86cfff)',
};

function domainColor(domain: string) {
  return DOMAIN_COLOR[domain] ?? KC.onSurfaceVariant;
}

// ── Edge type → colour ────────────────────────────────────────────────────────
const EDGE_COLOR: Record<string, string> = {
  depends_on: KC.orange,
  implements: KC.green,
  validates: KC.teal,
  contains: KC.onSurfaceVariant,
  versioned_by: 'var(--mf-c-a78bfa)',
  constrained_by: KC.amber,
  produced_by: KC.green,
  uses_component: KC.teal,
  parent_of: KC.onSurfaceVariant,
  conflicts_with: KC.red,
  // FORGE-704: the kinds the live graph holds that had no colour.
  satisfies: KC.green,
  supported_by: KC.teal,
  generated_from: 'var(--mf-c-a78bfa)',
  derives_from: KC.amber,
  supersedes: KC.red,
  refines: KC.teal,
  included_in_baseline: KC.onSurfaceVariant,
};

function edgeColor(type: string) {
  return EDGE_COLOR[type] ?? KC.onSurfaceVariant;
}

// ── Connection handles ────────────────────────────────────────────────────────
// FORGE-704. React Flow 12 anchors an edge on a node's handles, and a custom node
// that declares none cannot be connected to: every edge fails with error 008
// ("Couldn't create edge for source handle id: null") and nothing is drawn. This
// graph never had them, so it never drew a single link. They are invisible and
// inert: the graph is read-only, so there is nothing to drag from.
const HANDLE_STYLE = { opacity: 0, pointerEvents: 'none' } as const;

function NodeHandles() {
  return (
    <>
      <Handle type="target" position={Position.Left} isConnectable={false} style={HANDLE_STYLE} />
      <Handle type="source" position={Position.Right} isConnectable={false} style={HANDLE_STYLE} />
    </>
  );
}

// ── Custom node ───────────────────────────────────────────────────────────────
function TwinNode({ data }: { data: TwinNodeData }) {
  const accent = domainColor(data.domain);
  const icon = WP_TYPE_ICONS[data.wpType ?? ''] ?? 'description';

  return (
    <div
      style={{
        minWidth: 160,
        maxWidth: 200,
        background: KC.surfaceContainer,
        border: `1px solid ${data.selected ? accent : KC.border}`,
        borderRadius: 6,
        boxShadow: data.selected ? `0 0 0 2px ${accent}44` : 'none',
        cursor: 'pointer',
        fontFamily: 'monospace',
        fontSize: 11,
        overflow: 'hidden',
      }}
    >
      <NodeHandles />
      {/* Header strip */}
      <div
        style={{
          height: 3,
          background: accent,
          borderRadius: '6px 6px 0 0',
        }}
      />
      <div style={{ padding: '6px 10px', display: 'flex', alignItems: 'center', gap: 6 }}>
        <span
          className="material-symbols-outlined"
          style={{ fontSize: 16, color: accent, flexShrink: 0 }}
        >
          {icon}
        </span>
        <div style={{ flex: 1, overflow: 'hidden' }}>
          <div
            style={{
              color: data.selected ? KC.onSurface : KC.onSurface,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              fontWeight: data.selected ? 600 : 400,
            }}
          >
            {data.name}
          </div>
          <div style={{ color: KC.onSurfaceVariant, fontSize: 10, marginTop: 1 }}>
            {data.domain}
          </div>
        </div>
        {/* Status dot */}
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: data.status === 'valid' || data.status === 'active' ? KC.green : KC.onSurfaceVariant,
            flexShrink: 0,
          }}
        />
      </div>
    </div>
  );
}

interface TwinNodeData extends Record<string, unknown> {
  name: string;
  domain: string;
  wpType: string;
  status: string;
  selected: boolean;
}

// ── Linked record: the far end of an edge that is not a work product ─────────
// FORGE-704. A constraint, baseline, BOM item or engineering entity. Dashed and
// smaller than a work product so it reads as "what this is linked to", not as
// another thing to open.
const GROUP_ICON: Record<LinkGroup, string> = {
  constraints: 'rule',
  intent: 'flag',
  parts: 'memory',
  baselines: 'inventory_2',
  other: 'help',
};
const GROUP_COLOR: Record<LinkGroup, string> = {
  constraints: KC.amber,
  intent: KC.green,
  parts: KC.teal,
  baselines: KC.onSurfaceVariant,
  other: KC.onSurfaceVariant,
};

interface RefNodeData extends Record<string, unknown> {
  name: string;
  kind: string;
}

function RefNode({ data }: { data: RefNodeData }) {
  const group = groupOf(data.kind);
  const accent = GROUP_COLOR[group];
  return (
    <div
      data-testid="twin-ref-node"
      style={{
        minWidth: 140,
        maxWidth: 190,
        background: 'transparent',
        border: `1px dashed ${accent}`,
        borderRadius: 6,
        fontFamily: 'monospace',
        fontSize: 10,
        padding: '4px 8px',
        display: 'flex',
        alignItems: 'center',
        gap: 6,
      }}
    >
      <NodeHandles />
      <span className="material-symbols-outlined" style={{ fontSize: 14, color: accent, flexShrink: 0 }}>
        {GROUP_ICON[group]}
      </span>
      <div style={{ overflow: 'hidden' }}>
        <div style={{ color: KC.onSurfaceVariant, textTransform: 'uppercase', letterSpacing: 0.4 }}>
          {data.kind.replace(/_/g, ' ')}
        </div>
        <div
          title={data.name}
          style={{ color: KC.onSurface, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
        >
          {data.name}
        </div>
      </div>
    </div>
  );
}

const nodeTypes = { twin: TwinNode, ref: RefNode };

// ── Layout: simple grid ───────────────────────────────────────────────────────
const COL_W = 220;
const ROW_H = 90;

// The page overlays a floating node-detail panel at `right:72, width:320`
// (392px total) whenever a node is selected. react-flow's own fitView has no
// way to reserve space asymmetrically for that, so a freshly-selected node
// can end up laid out underneath it — most visibly at narrower viewports
// where 392px is a much bigger share of the canvas. Panning the selected
// node left by half that reserved width keeps it clear of the panel.
const DETAIL_PANEL_RESERVED_PX = 392;
const DEFAULT_NODE_WIDTH = 180; // TwinNode's min/maxWidth midpoint, if unmeasured
const DEFAULT_NODE_HEIGHT = 52;

/** A column holds at most this many work products before it wraps to the next. */
const MAX_ROWS = 8;
/** Linked records are shorter, so a column of them holds more. */
const GHOST_ROWS = 12;

/**
 * Work products go in layers by dependency depth, so an edge mostly runs one
 * column to the right instead of up and down a domain's tall column. A layer
 * longer than MAX_ROWS wraps into more columns. Cycles are tolerated: depth is
 * relaxed in a bounded number of passes, so a loop cannot run away.
 */
function layoutNodes(
  twinNodes: TwinNode[],
  relationships: TwinRelationship[],
): { positions: Map<string, { x: number; y: number }>; columns: number } {
  const ids = new Set(twinNodes.map((n) => n.id));
  const depth = new Map<string, number>(twinNodes.map((n) => [n.id, 0]));
  const edges = relationships.filter(
    (r) => r.sourceId !== r.targetId && ids.has(r.sourceId) && ids.has(r.targetId),
  );
  for (let pass = 0; pass < Math.min(twinNodes.length, 12); pass++) {
    let moved = false;
    for (const r of edges) {
      const want = (depth.get(r.sourceId) ?? 0) + 1;
      if (want > (depth.get(r.targetId) ?? 0) && want <= twinNodes.length) {
        depth.set(r.targetId, want);
        moved = true;
      }
    }
    if (!moved) break;
  }

  const layers = new Map<number, TwinNode[]>();
  for (const n of twinNodes) {
    const d = depth.get(n.id) ?? 0;
    const layer = layers.get(d);
    if (layer) layer.push(n);
    else layers.set(d, [n]);
  }

  const positions = new Map<string, { x: number; y: number }>();
  let col = 0;
  for (const d of [...layers.keys()].sort((a, b) => a - b)) {
    const layer = (layers.get(d) ?? []).sort(
      (a, b) => a.domain.localeCompare(b.domain) || a.name.localeCompare(b.name),
    );
    layer.forEach((n, i) => {
      positions.set(n.id, { x: (col + Math.floor(i / MAX_ROWS)) * COL_W, y: (i % MAX_ROWS) * ROW_H });
    });
    col += Math.max(1, Math.ceil(layer.length / MAX_ROWS));
  }
  return { positions, columns: col };
}

/** Linked records go in columns to the right of the work products, grouped by kind. */
function layoutGhosts(
  ghosts: Record<string, TwinEndpoint>,
  workProductColumns: number,
): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>();
  const ordered = Object.entries(ghosts).sort(
    ([, a], [, b]) => groupOf(a.kind).localeCompare(groupOf(b.kind)) || a.name.localeCompare(b.name),
  );
  const x0 = (workProductColumns + 0.4) * COL_W;
  ordered.forEach(([id], i) =>
    positions.set(id, {
      x: x0 + Math.floor(i / GHOST_ROWS) * COL_W,
      y: (i % GHOST_ROWS) * (ROW_H * 0.6),
    }),
  );
  return positions;
}

// ── Props ─────────────────────────────────────────────────────────────────────
interface TwinGraphCanvasProps {
  nodes: TwinNode[];
  relationships: TwinRelationship[];
  /** FORGE-704: the far ends of `relationships` that are not work products. */
  ghosts?: Record<string, TwinEndpoint>;
  selectedId: string | null;
  onSelectNode: (id: string | null) => void;
}

// ── Main component ────────────────────────────────────────────────────────────
export function TwinGraphCanvas({
  nodes: twinNodes,
  relationships,
  ghosts,
  selectedId,
  onSelectNode,
}: TwinGraphCanvasProps) {
  const rfInstanceRef = useRef<ReactFlowInstance<Node<TwinNodeData | RefNodeData>, Edge> | null>(null);

  const layout = useMemo(() => layoutNodes(twinNodes, relationships), [twinNodes, relationships]);
  const positions = layout.positions;
  const ghostMap = useMemo(() => ghosts ?? {}, [ghosts]);
  const ghostPositions = useMemo(
    () => layoutGhosts(ghostMap, layout.columns),
    [ghostMap, layout.columns],
  );

  const workProductNodes: Node<TwinNodeData>[] = useMemo(
    () =>
      twinNodes.map((n) => {
        const pos = positions.get(n.id) ?? { x: 0, y: 0 };
        return {
          id: n.id,
          type: 'twin',
          position: pos,
          sourcePosition: Position.Right,
          targetPosition: Position.Left,
          data: {
            name: n.name,
            domain: n.domain,
            wpType: (n.properties?.wp_type as string) ?? '',
            status: n.status,
            selected: n.id === selectedId,
          },
        };
      }),
    [twinNodes, positions, selectedId],
  );

  const linkedNodes: Node<RefNodeData>[] = useMemo(
    () =>
      Object.entries(ghostMap).map(([id, g]) => ({
        id,
        type: 'ref',
        position: ghostPositions.get(id) ?? { x: 0, y: 0 },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        draggable: true,
        selectable: false,
        data: { name: g.name, kind: g.kind },
      })),
    [ghostMap, ghostPositions],
  );
  const rfNodes = useMemo(
    () => [...workProductNodes, ...linkedNodes] as Node<TwinNodeData | RefNodeData>[],
    [workProductNodes, linkedNodes],
  );

  const rfEdges: Edge[] = useMemo(
    () =>
      relationships.map((r) => {
        const color = edgeColor(r.type);
        const toLinkedRecord = r.sourceId in ghostMap || r.targetId in ghostMap;
        return {
          id: r.id,
          source: r.sourceId,
          target: r.targetId,
          // Labels on 140 edges are clutter; name only the links of the selected
          // node. The rest are told apart by colour.
          label: selectedId && (r.sourceId === selectedId || r.targetId === selectedId) ? r.label : undefined,
          labelStyle: { fill: KC.onSurfaceVariant, fontSize: 10, fontFamily: 'monospace' },
          labelBgStyle: { fill: KC.surface, fillOpacity: 0.85 },
          style: {
            stroke: color,
            strokeWidth: 1.5,
            // A link to a record that is not a work product reads as lighter.
            ...(toLinkedRecord ? { strokeDasharray: '4 3', opacity: 0.8 } : {}),
          },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            width: 10,
            height: 10,
            color,
          },
          animated: r.type === 'produced_by' || r.type === 'validates',
        };
      }),
    [relationships, ghostMap, selectedId],
  );

  const onNodeClick: NodeMouseHandler = useCallback(
    (_evt, node) => {
      // A linked record is not a work product: there is nothing to open.
      if (node.type === 'ref') return;
      onSelectNode(node.id === selectedId ? null : node.id);
    },
    [selectedId, onSelectNode],
  );

  const onPaneClick = useCallback(() => onSelectNode(null), [onSelectNode]);

  // Keep the selected node clear of the floating detail panel by shifting
  // the viewport's center to the right (which moves the node left on
  // screen) by half the panel's reserved width, converted to flow units.
  useEffect(() => {
    const instance = rfInstanceRef.current;
    if (!instance || !selectedId) return;
    const pos = positions.get(selectedId);
    if (!pos) return;
    const measured = instance.getNode(selectedId)?.measured;
    const nodeW = measured?.width ?? DEFAULT_NODE_WIDTH;
    const nodeH = measured?.height ?? DEFAULT_NODE_HEIGHT;
    const { zoom } = instance.getViewport();
    const offsetX = (DETAIL_PANEL_RESERVED_PX / 2) / zoom;
    instance.setCenter(pos.x + nodeW / 2 + offsetX, pos.y + nodeH / 2, {
      zoom,
      duration: 300,
    });
  }, [selectedId, positions]);

  return (
    <div style={{ width: '100%', flex: 1, minHeight: 0 }}>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        onInit={(instance) => { rfInstanceRef.current = instance; }}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        colorMode="dark"
        style={{ background: 'transparent' }}
        proOptions={{ hideAttribution: true }}
      >
        <Background
          color="var(--mf-r-154-154-170-0p12)"
          gap={32}
          size={1}
          style={{ background: 'transparent' }}
        />
        <Controls
          style={{
            background: KC.surfaceContainer,
            border: `1px solid ${KC.border}`,
            borderRadius: 6,
            // The page overlays a floating "Nodes" list panel at bottom-left
            // (left:16, bottom:80) on top of this canvas — react-flow's
            // default bottom-left offset (~10px) puts the (multi-button,
            // ~106px tall) control stack squarely under that panel. Clear
            // its footprint with margin to spare.
            bottom: 96,
            left: 16,
          }}
        />
        <MiniMap
          nodeColor={(n) =>
            n.type === 'ref'
              ? GROUP_COLOR[groupOf((n.data as RefNodeData).kind)]
              : domainColor((n.data as TwinNodeData).domain)
          }
          style={{
            background: KC.surfaceContainer,
            border: `1px solid ${KC.border}`,
            borderRadius: 6,
          }}
          maskColor="rgba(0,0,0,0.5)"
        />
      </ReactFlow>
    </div>
  );
}
