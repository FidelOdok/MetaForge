/**
 * TwinGraphCanvas — interactive Digital Thread graph using @xyflow/react.
 *
 * Renders work-product nodes and typed relationship edges fetched from the
 * Twin API. Nodes are laid out in a simple left-to-right grid; users can
 * pan, zoom, and click to select nodes.
 */

import { useCallback, useMemo } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  type Node,
  type Edge,
  type NodeMouseHandler,
  Position,
  MarkerType,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { TwinNode, TwinRelationship } from '../../types/twin';

// ── KC tokens ─────────────────────────────────────────────────────────────────
const KC = {
  surface: '#111319',
  surfaceContainer: 'rgba(30,31,38,0.92)',
  surfaceHigh: '#282a30',
  border: 'rgba(65,72,90,0.35)',
  onSurface: '#e2e2eb',
  onSurfaceVariant: '#9a9aaa',
  orange: '#e67e22',
  green: '#3dd68c',
  teal: '#86cfff',
  amber: '#f5a623',
  red: '#e74c3c',
} as const;

// ── Domain → accent colour ────────────────────────────────────────────────────
const DOMAIN_COLOR: Record<string, string> = {
  mechanical: KC.teal,
  electronics: KC.amber,
  firmware: KC.green,
  simulation: '#a78bfa',
  compliance: '#f472b6',
  supply_chain: '#34d399',
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
  versioned_by: '#a78bfa',
  constrained_by: KC.amber,
  produced_by: KC.green,
  uses_component: KC.teal,
  parent_of: KC.onSurfaceVariant,
  conflicts_with: KC.red,
};

function edgeColor(type: string) {
  return EDGE_COLOR[type] ?? KC.onSurfaceVariant;
}

// ── Work-product type → icon name ─────────────────────────────────────────────
const WP_ICONS: Record<string, string> = {
  schematic: 'schema',
  pcb_layout: 'developer_board',
  bom: 'table_rows',
  cad_model: 'view_in_ar',
  firmware_source: 'memory',
  simulation_result: 'bar_chart',
  test_plan: 'checklist',
  test_result: 'fact_check',
  manufacturing_file: 'precision_manufacturing',
  gerber: 'layers',
  pinmap: 'share',
  prd: 'description',
  constraint_set: 'rule',
  pick_and_place: 'place',
  documentation: 'article',
};

// ── Custom node ───────────────────────────────────────────────────────────────
function TwinNode({ data }: { data: TwinNodeData }) {
  const accent = domainColor(data.domain);
  const icon = WP_ICONS[data.wpType ?? ''] ?? 'description';

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

const nodeTypes = { twin: TwinNode };

// ── Layout: simple grid ───────────────────────────────────────────────────────
const COL_W = 220;
const ROW_H = 90;

function layoutNodes(twinNodes: TwinNode[]): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>();
  // Group by domain for visual clustering
  const byDomain: Record<string, TwinNode[]> = {};
  for (const n of twinNodes) {
    (byDomain[n.domain] ??= []).push(n);
  }
  let col = 0;
  for (const nodes of Object.values(byDomain)) {
    nodes.forEach((n, row) => {
      positions.set(n.id, { x: col * COL_W, y: row * ROW_H });
    });
    col++;
  }
  return positions;
}

// ── Props ─────────────────────────────────────────────────────────────────────
interface TwinGraphCanvasProps {
  nodes: TwinNode[];
  relationships: TwinRelationship[];
  selectedId: string | null;
  onSelectNode: (id: string | null) => void;
}

// ── Main component ────────────────────────────────────────────────────────────
export function TwinGraphCanvas({
  nodes: twinNodes,
  relationships,
  selectedId,
  onSelectNode,
}: TwinGraphCanvasProps) {
  const positions = useMemo(() => layoutNodes(twinNodes), [twinNodes]);

  const rfNodes: Node<TwinNodeData>[] = useMemo(
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

  const rfEdges: Edge[] = useMemo(
    () =>
      relationships.map((r) => {
        const color = edgeColor(r.type);
        return {
          id: r.id,
          source: r.sourceId,
          target: r.targetId,
          label: r.label,
          labelStyle: { fill: KC.onSurfaceVariant, fontSize: 10, fontFamily: 'monospace' },
          labelBgStyle: { fill: KC.surface, fillOpacity: 0.85 },
          style: { stroke: color, strokeWidth: 1.5 },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            width: 10,
            height: 10,
            color,
          },
          animated: r.type === 'produced_by' || r.type === 'validates',
        };
      }),
    [relationships],
  );

  const onNodeClick: NodeMouseHandler = useCallback(
    (_evt, node) => {
      onSelectNode(node.id === selectedId ? null : node.id);
    },
    [selectedId, onSelectNode],
  );

  const onPaneClick = useCallback(() => onSelectNode(null), [onSelectNode]);

  return (
    <div style={{ width: '100%', height: '100%' }}>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        colorMode="dark"
        style={{ background: 'transparent' }}
        proOptions={{ hideAttribution: true }}
      >
        <Background
          color="rgba(154,154,170,0.12)"
          gap={32}
          size={1}
          style={{ background: 'transparent' }}
        />
        <Controls
          style={{
            background: KC.surfaceContainer,
            border: `1px solid ${KC.border}`,
            borderRadius: 6,
          }}
        />
        <MiniMap
          nodeColor={(n) => domainColor((n.data as TwinNodeData).domain)}
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
