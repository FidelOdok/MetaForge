/**
 * The flow graph on the run view (FORGE-396).
 *
 * Phases and gates as nodes, laid out left to right, coloured by status.
 * Clicking a phase selects it; the activity lane beside the graph shows what
 * that phase did.
 *
 * Two things this deliberately does *not* do:
 *
 * - **It never infers a status.** A phase whose state could not be read is
 *   drawn as `unknown`, visibly different from `pending`. An engine that
 *   cannot be queried and a flow that has not started yet look identical if
 *   you let "no data" render as "not yet" — and one of those is an outage.
 * - **It does not decide whether a gate is answerable.** The parent passes
 *   that in. A second opinion here would be a second answer to "can this be
 *   approved", and the disagreeing copy is the one that shows a button which
 *   does nothing.
 */
import { useMemo } from 'react';
import {
  Background,
  Controls,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeMouseHandler,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import type { FlowPhaseState } from '../../types/run-flow';

const KC = {
  surfaceContainer: 'var(--mf-r-30-31-38-0p92)',
  surfaceHigh: 'var(--mf-c-282a30)',
  border: 'var(--mf-r-65-72-90-0p35)',
  onSurface: 'var(--mf-c-e2e2eb)',
  onSurfaceVariant: 'var(--mf-c-9a9aaa)',
  green: 'var(--mf-c-3dd68c)',
  teal: 'var(--mf-c-86cfff)',
  amber: 'var(--mf-c-f5a623)',
  red: 'var(--mf-c-e74c3c)',
} as const;

/** Colour and label per status. `unknown` is grey *and* says so. */
const STATUS: Record<string, { color: string; label: string; icon: string }> = {
  pending: { color: KC.onSurfaceVariant, label: 'PENDING', icon: 'schedule' },
  running: { color: KC.teal, label: 'RUNNING', icon: 'play_circle' },
  awaiting_gate: { color: KC.amber, label: 'WAITING ON GATE', icon: 'pause_circle' },
  passed: { color: KC.green, label: 'PASSED', icon: 'check_circle' },
  failed: { color: KC.red, label: 'FAILED', icon: 'error' },
  rejected: { color: KC.red, label: 'REJECTED', icon: 'block' },
  unknown: { color: KC.onSurfaceVariant, label: 'UNKNOWN', icon: 'help' },
};

function statusOf(status: string) {
  return STATUS[status] ?? STATUS.unknown!;
}

export interface PhaseNodeData extends Record<string, unknown> {
  phase: FlowPhaseState;
  selected: boolean;
  canApprove: boolean;
  onApprove?: (decision: 'approve' | 'reject') => void;
}

function PhaseNode({ data }: { data: PhaseNodeData }) {
  const { phase, selected, canApprove, onApprove } = data;
  const look = statusOf(phase.status);

  return (
    <div
      data-testid={`phase-node-${phase.id}`}
      data-status={phase.status}
      style={{
        minWidth: 190,
        background: KC.surfaceContainer,
        border: `1px solid ${selected ? look.color : KC.border}`,
        boxShadow: selected ? `0 0 0 2px ${look.color}44` : 'none',
        borderRadius: 6,
        overflow: 'hidden',
        cursor: 'pointer',
        fontFamily: 'monospace',
        fontSize: 11,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '6px 10px',
          borderBottom: `1px solid ${KC.border}`,
          background: KC.surfaceHigh,
        }}
      >
        <span className="material-symbols-outlined" style={{ fontSize: 13, color: look.color }}>
          {look.icon}
        </span>
        <span style={{ color: KC.onSurface }}>{phase.title}</span>
      </div>

      <div style={{ padding: '6px 10px' }}>
        <div style={{ color: look.color, letterSpacing: '0.08em', fontSize: 9 }}>
          {look.label}
        </div>
        {phase.artifacts.length > 0 && (
          <div style={{ color: KC.onSurfaceVariant, marginTop: 4 }}>
            {phase.artifacts.length} artifact{phase.artifacts.length === 1 ? '' : 's'}
          </div>
        )}
        {phase.disciplines.length > 0 && (
          <div style={{ color: KC.onSurfaceVariant, marginTop: 2 }}>
            {phase.disciplines.join(' · ')}
          </div>
        )}
      </div>

      {/* The gate card, on the graph. Approving here resumes the run without
          navigating away from the thing being approved. */}
      {phase.gate && (
        <div
          style={{
            padding: '6px 10px',
            borderTop: `1px solid ${KC.border}`,
            background: phase.status === 'awaiting_gate' ? `${KC.amber}14` : 'transparent',
          }}
        >
          <div style={{ color: KC.onSurfaceVariant, fontSize: 9, letterSpacing: '0.08em' }}>
            GATE
          </div>
          <div style={{ color: KC.onSurface }}>{phase.gate}</div>
          {phase.status === 'awaiting_gate' && canApprove && (
            <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
              <button
                type="button"
                aria-label={`Approve ${phase.gate}`}
                onClick={(e) => {
                  e.stopPropagation();
                  onApprove?.('approve');
                }}
              >
                Approve
              </button>
              <button
                type="button"
                aria-label={`Reject ${phase.gate}`}
                onClick={(e) => {
                  e.stopPropagation();
                  onApprove?.('reject');
                }}
              >
                Reject
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const nodeTypes = { phase: PhaseNode };

export interface FlowGraphProps {
  phases: FlowPhaseState[];
  selectedPhaseId: string | null;
  onSelectPhase: (phaseId: string) => void;
  canApprove: boolean;
  onApprove?: (decision: 'approve' | 'reject') => void;
}

export function FlowGraph({
  phases,
  selectedPhaseId,
  onSelectPhase,
  canApprove,
  onApprove,
}: FlowGraphProps) {
  const nodes: Node<PhaseNodeData>[] = useMemo(
    () =>
      phases.map((phase, i) => ({
        id: phase.id,
        type: 'phase',
        // Disciplines fan a phase onto its own row, so parallel branches read
        // as parallel rather than as a longer queue.
        position: { x: i * 250, y: phase.disciplines.length > 1 ? 90 : 0 },
        data: {
          phase,
          selected: phase.id === selectedPhaseId,
          canApprove,
          onApprove,
        },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        draggable: false,
      })),
    [phases, selectedPhaseId, canApprove, onApprove],
  );

  const edges: Edge[] = useMemo(
    () =>
      phases.slice(1).map((phase, i) => ({
        id: `${phases[i]!.id}->${phase.id}`,
        source: phases[i]!.id,
        target: phase.id,
        markerEnd: { type: MarkerType.ArrowClosed },
        style: {
          stroke: phase.status === 'pending' ? KC.border : statusOf(phase.status).color,
        },
      })),
    [phases],
  );

  const onNodeClick: NodeMouseHandler = (_event, node) => onSelectPhase(node.id);

  return (
    <div style={{ height: 320 }} data-testid="flow-graph">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Background />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
