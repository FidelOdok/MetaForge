import { Link } from 'react-router-dom';
import { formatRelativeTime } from '../utils/format-time';
import { useSessions } from '../hooks/use-sessions';
import type { AgentSession } from '../types/session';

// KC color tokens
const KC = {
  surfaceContainer: 'rgba(30,31,38,0.85)',
  surfaceHigh: '#282a30',
  surfaceBorder: 'rgba(65,72,90,0.2)',
  onSurface: '#e2e2eb',
  onSurfaceVariant: '#9a9aaa',
  running: '#e67e22',
  done: '#3dd68c',
  pending: '#9a9aaa',
  error: '#ffb4ab',
  logBg: '#0a0b10',
} as const;

// Glass panel style
const glassPanel: React.CSSProperties = {
  background: KC.surfaceContainer,
  backdropFilter: 'blur(16px)',
  WebkitBackdropFilter: 'blur(16px)',
  borderRadius: 4,
  border: `1px solid ${KC.surfaceBorder}`,
};

// Panel header style
const panelHeader: React.CSSProperties = {
  height: 36,
  borderBottom: `1px solid ${KC.surfaceBorder}`,
  padding: '0 16px',
  display: 'flex',
  alignItems: 'center',
};

function statusDotColor(status: AgentSession['status']): string {
  switch (status) {
    case 'running': return KC.running;
    case 'completed': return KC.done;
    case 'failed': return KC.error;
    case 'pending': return KC.pending;
    default: return KC.pending;
  }
}

// --- DAG Node ---

type DagNodeStatus = 'DONE' | 'RUNNING' | 'QUEUED';

interface DagNode {
  label: string;
  status: DagNodeStatus;
}

function dagNodeStyle(status: DagNodeStatus): React.CSSProperties {
  switch (status) {
    case 'DONE':
      return {
        background: 'rgba(61,214,140,0.1)',
        border: '1px solid rgba(61,214,140,0.35)',
        color: '#3dd68c',
      };
    case 'RUNNING':
      return {
        background: 'rgba(230,126,34,0.12)',
        border: '1px solid rgba(230,126,34,0.55)',
        color: '#e67e22',
      };
    case 'QUEUED':
    default:
      return {
        background: 'rgba(65,72,90,0.1)',
        border: '1px dashed rgba(65,72,90,0.4)',
        color: '#9a9aaa',
      };
  }
}

function DagNodeIcon({ status }: { status: DagNodeStatus }) {
  if (status === 'DONE') {
    return (
      <span
        className="material-symbols-outlined"
        style={{ fontSize: 14, color: '#3dd68c', lineHeight: 1 }}
      >
        check_circle
      </span>
    );
  }
  if (status === 'RUNNING') {
    return (
      <span
        className="material-symbols-outlined"
        style={{
          fontSize: 14,
          color: '#e67e22',
          lineHeight: 1,
          display: 'inline-block',
          animation: 'spin 1s linear infinite',
        }}
      >
        refresh
      </span>
    );
  }
  return (
    <span
      className="material-symbols-outlined"
      style={{ fontSize: 14, color: '#9a9aaa', lineHeight: 1 }}
    >
      schedule
    </span>
  );
}

function DagNodePill({ node }: { node: DagNode }) {
  const style = dagNodeStyle(node.status);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
      <div
        style={{
          ...style,
          height: 36,
          padding: '0 12px',
          borderRadius: 4,
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          whiteSpace: 'nowrap',
          fontSize: 11,
          fontFamily: 'inherit',
        }}
      >
        <DagNodeIcon status={node.status} />
        <span>{node.label}</span>
      </div>
      <span
        style={{
          fontSize: 9,
          fontFamily: 'Roboto Mono, monospace',
          textTransform: 'uppercase',
          color: style.color as string,
          letterSpacing: '0.06em',
        }}
      >
        {node.status}
      </span>
    </div>
  );
}

function DagConnector({ from, to }: { from: DagNodeStatus; to: DagNodeStatus }) {
  const isDoneToRunning = from === 'DONE' && to === 'RUNNING';
  const isDoneToDone = from === 'DONE' && to === 'DONE';
  const color = isDoneToDone ? '#3dd68c' : isDoneToRunning ? '#e67e22' : 'rgba(65,72,90,0.4)';
  const dashed = !isDoneToDone && !isDoneToRunning;

  return (
    <div
      style={{
        flex: 1,
        minWidth: 16,
        height: 1,
        marginBottom: 18, // align with node center (accounts for label below)
        background: dashed ? 'transparent' : color,
        borderTop: dashed ? `1px dashed ${color}` : undefined,
      }}
    />
  );
}

function sessionToDagStatus(status: AgentSession['status']): DagNodeStatus {
  if (status === 'completed') return 'DONE';
  if (status === 'running') return 'RUNNING';
  return 'QUEUED';
}

const STATIC_DAG_NODES: DagNode[] = [
  { label: 'spec', status: 'DONE' },
  { label: 'architecture', status: 'DONE' },
  { label: 'schematic', status: 'RUNNING' },
  { label: 'bom', status: 'QUEUED' },
  { label: 'test-plan', status: 'QUEUED' },
];

function DagPanel({ sessions }: { sessions: AgentSession[] }) {
  const runningSessions = sessions.filter((s) => s.status === 'running');
  const activeLabel =
    runningSessions.length > 0 && runningSessions[0]
      ? runningSessions[0].taskType.replace(/_/g, '-')
      : 'spec → bom';

  const isRunning = runningSessions.length > 0;

  const nodes: DagNode[] =
    sessions.length > 0
      ? sessions.slice(0, 5).map((s) => ({
          label: s.taskType.replace(/_/g, '-'),
          status: sessionToDagStatus(s.status),
        }))
      : STATIC_DAG_NODES;

  return (
    <div style={{ ...glassPanel }}>
      {/* Header */}
      <div style={{ ...panelHeader, justifyContent: 'space-between' }}>
        <span
          style={{
            fontFamily: 'Roboto Mono, monospace',
            fontSize: 10,
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
            color: KC.onSurfaceVariant,
          }}
        >
          ACTIVE WORKFLOW — {activeLabel}
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span
            style={{
              display: 'inline-block',
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: isRunning ? '#3dd68c' : '#9a9aaa',
              flexShrink: 0,
            }}
          />
          <span
            style={{
              fontFamily: 'Roboto Mono, monospace',
              fontSize: 10,
              letterSpacing: '0.08em',
              color: isRunning ? '#3dd68c' : '#9a9aaa',
            }}
          >
            {isRunning ? 'RUNNING' : 'IDLE'}
          </span>
        </div>
      </div>

      {/* DAG visualization */}
      <div
        style={{
          minHeight: 160,
          padding: 16,
          display: 'flex',
          alignItems: 'center',
          overflowX: 'auto',
        }}
      >
        {nodes.map((node, i) => (
          <div key={node.label} style={{ display: 'flex', alignItems: 'center', flex: i < nodes.length - 1 ? undefined : 0 }}>
            <DagNodePill node={node} />
            {i < nodes.length - 1 && nodes[i + 1] && (
              <DagConnector from={node.status} to={nodes[i + 1]!.status} />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// --- Execution Log ---

function logBadgeStyle(status: AgentSession['status']): React.CSSProperties {
  switch (status) {
    case 'running':
      return {
        background: 'rgba(134,207,255,0.12)',
        color: '#86cfff',
        padding: '0 5px',
        borderRadius: 3,
        fontSize: 9,
        fontFamily: 'Roboto Mono, monospace',
        letterSpacing: '0.06em',
      };
    case 'completed':
      return {
        background: 'rgba(61,214,140,0.1)',
        color: '#3dd68c',
        padding: '0 5px',
        borderRadius: 3,
        fontSize: 9,
        fontFamily: 'Roboto Mono, monospace',
        letterSpacing: '0.06em',
      };
    case 'failed':
      return {
        background: 'rgba(255,180,171,0.12)',
        color: '#ffb4ab',
        padding: '0 5px',
        borderRadius: 3,
        fontSize: 9,
        fontFamily: 'Roboto Mono, monospace',
        letterSpacing: '0.06em',
      };
    default:
      return {
        background: 'rgba(134,207,255,0.12)',
        color: '#86cfff',
        padding: '0 5px',
        borderRadius: 3,
        fontSize: 9,
        fontFamily: 'Roboto Mono, monospace',
        letterSpacing: '0.06em',
      };
  }
}

function logLevelLabel(status: AgentSession['status']): string {
  return status === 'failed' ? 'ERROR' : 'INFO';
}

const STATIC_LOG_LINES = [
  { ts: '12:04:01', level: 'INFO', msg: 'Workflow spec→bom started — runId wf_0x1a2b3c' },
  { ts: '12:04:02', level: 'INFO', msg: 'Agent requirements-agent picked up task spec' },
  { ts: '12:04:18', level: 'INFO', msg: 'Task spec completed in 16s — status DONE' },
  { ts: '12:04:19', level: 'WARN', msg: 'schematic agent retrying — tool timeout (attempt 1/3)' },
];

function ExecutionLogPanel({ sessions }: { sessions: AgentSession[] }) {
  return (
    <div style={{ ...glassPanel }}>
      {/* Header */}
      <div style={{ ...panelHeader, justifyContent: 'space-between' }}>
        <span
          style={{
            fontFamily: 'Roboto Mono, monospace',
            fontSize: 10,
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
            color: KC.onSurfaceVariant,
          }}
        >
          EXECUTION LOG
        </span>
        <button
          style={{
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            fontFamily: 'Roboto Mono, monospace',
            fontSize: 9,
            letterSpacing: '0.08em',
            color: KC.onSurfaceVariant,
            textTransform: 'uppercase',
            padding: '2px 6px',
          }}
        >
          CLEAR
        </button>
      </div>

      {/* Log area */}
      <div
        style={{
          background: KC.logBg,
          padding: '12px 14px',
          minHeight: 120,
          maxHeight: 200,
          overflowY: 'auto',
          fontSize: 11,
          fontFamily: 'Roboto Mono, monospace',
          lineHeight: 1.8,
        }}
      >
        {sessions.length > 0
          ? sessions.map((s) => (
              <div key={s.id} style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                <span style={{ color: KC.onSurfaceVariant, flexShrink: 0 }}>
                  {new Date(s.startedAt).toLocaleTimeString('en-GB', { hour12: false })}
                </span>
                <span style={logBadgeStyle(s.status)}>{logLevelLabel(s.status)}</span>
                <span style={{ color: KC.onSurface }}>
                  [{s.agentCode}] Task {s.taskType.replace(/_/g, '-')} — {s.status}
                </span>
              </div>
            ))
          : STATIC_LOG_LINES.map((line, i) => (
              <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                <span style={{ color: KC.onSurfaceVariant, flexShrink: 0 }}>{line.ts}</span>
                <span
                  style={{
                    background:
                      line.level === 'ERROR'
                        ? 'rgba(255,180,171,0.12)'
                        : line.level === 'WARN'
                        ? 'rgba(255,200,120,0.12)'
                        : 'rgba(134,207,255,0.12)',
                    color:
                      line.level === 'ERROR'
                        ? '#ffb4ab'
                        : line.level === 'WARN'
                        ? '#ffc878'
                        : '#86cfff',
                    padding: '0 5px',
                    borderRadius: 3,
                    fontSize: 9,
                    letterSpacing: '0.06em',
                    flexShrink: 0,
                  }}
                >
                  {line.level}
                </span>
                <span style={{ color: KC.onSurface }}>{line.msg}</span>
              </div>
            ))}
      </div>
    </div>
  );
}

// --- Pending Approval card ---

function PendingApprovalCard() {
  return (
    <div style={{ ...glassPanel }}>
      {/* Header */}
      <div style={{ ...panelHeader, justifyContent: 'space-between' }}>
        <span
          style={{
            fontFamily: 'Roboto Mono, monospace',
            fontSize: 10,
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
            color: KC.onSurfaceVariant,
          }}
        >
          PENDING APPROVAL
        </span>
        <span
          style={{
            display: 'inline-block',
            width: 7,
            height: 7,
            borderRadius: '50%',
            background: '#f0a500',
            animation: 'pulse 1.8s ease-in-out infinite',
          }}
        />
      </div>

      {/* Diff preview */}
      <div
        style={{
          padding: '10px 14px',
          fontFamily: 'Roboto Mono, monospace',
          fontSize: 11,
          lineHeight: 1.9,
          background: KC.logBg,
          borderBottom: `1px solid ${KC.surfaceBorder}`,
        }}
      >
        <div style={{ color: '#3dd68c' }}>+ NET STM32_PA9 (PWR_FLAG)</div>
        <div style={{ color: '#3dd68c' }}>+ COMPONENT U3 STM32H743VIT6</div>
        <div style={{ color: '#ffb4ab' }}>- COMPONENT U3 STM32F405RGT6</div>
      </div>

      {/* Buttons */}
      <div style={{ padding: '10px 14px', display: 'flex', gap: 8 }}>
        <button
          style={{
            flex: 1,
            height: 30,
            background: KC.running,
            border: 'none',
            borderRadius: 4,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 4,
            fontSize: 11,
            color: '#fff',
            fontFamily: 'inherit',
          }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 14 }}>check</span>
          Approve
        </button>
        <button
          style={{
            flex: 1,
            height: 30,
            background: 'rgba(65,72,90,0.35)',
            border: `1px solid ${KC.surfaceBorder}`,
            borderRadius: 4,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 4,
            fontSize: 11,
            color: KC.onSurface,
            fontFamily: 'inherit',
          }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 14 }}>close</span>
          Reject
        </button>
      </div>

      {/* Link */}
      <div style={{ padding: '0 14px 10px', textAlign: 'center' }}>
        <Link
          to="/approvals"
          style={{
            fontFamily: 'Roboto Mono, monospace',
            fontSize: 10,
            color: KC.onSurfaceVariant,
            textDecoration: 'none',
          }}
        >
          View all approvals →
        </Link>
      </div>
    </div>
  );
}

// --- Agent Roster ---

interface RosterAgent {
  icon: string;
  name: string;
  dotColor: string;
  dotPulse: boolean;
  statusLabel: string;
}

const ROSTER_AGENTS: RosterAgent[] = [
  { icon: 'description', name: 'Requirements Agent', dotColor: '#3dd68c', dotPulse: true, statusLabel: 'running spec' },
  { icon: 'precision_manufacturing', name: 'Mechanical Agent', dotColor: '#9a9aaa', dotPulse: false, statusLabel: 'idle' },
  { icon: 'memory', name: 'Electronics Agent', dotColor: '#f0a500', dotPulse: false, statusLabel: 'waiting' },
  { icon: 'calculate', name: 'Simulation Agent', dotColor: '#9a9aaa', dotPulse: false, statusLabel: 'idle' },
];

function AgentRosterPanel() {
  return (
    <div style={{ ...glassPanel }}>
      {/* Header */}
      <div style={panelHeader}>
        <span
          style={{
            fontFamily: 'Roboto Mono, monospace',
            fontSize: 10,
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
            color: KC.onSurfaceVariant,
          }}
        >
          AGENT ROSTER
        </span>
      </div>

      {/* Agent rows */}
      {ROSTER_AGENTS.map((agent) => (
        <div
          key={agent.name}
          style={{
            height: 36,
            padding: '0 14px',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            borderBottom: `1px solid rgba(65,72,90,0.08)`,
          }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 16, color: KC.onSurfaceVariant }}>
            {agent.icon}
          </span>
          <span style={{ flex: 1, fontSize: 12, color: KC.onSurface }}>{agent.name}</span>
          <span
            style={{
              display: 'inline-block',
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: agent.dotColor,
              flexShrink: 0,
              animation: agent.dotPulse ? 'pulse 1.8s ease-in-out infinite' : undefined,
            }}
          />
          <span
            style={{
              fontFamily: 'Roboto Mono, monospace',
              fontSize: 10,
              color: agent.dotColor,
              minWidth: 60,
              textAlign: 'right',
            }}
          >
            {agent.statusLabel}
          </span>
        </div>
      ))}
    </div>
  );
}

// --- SessionRow (kept for completeness) ---

function SessionRow({ session }: { session: AgentSession }) {
  return (
    <Link
      to={`/sessions/${session.id}`}
      style={{ textDecoration: 'none', display: 'block' }}
    >
      <div
        style={{
          height: 40,
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          padding: '0 16px',
          borderBottom: `1px solid rgba(65,72,90,0.08)`,
          color: KC.onSurface,
        }}
        onMouseEnter={(e) => {
          (e.currentTarget as HTMLDivElement).style.background = KC.surfaceHigh;
        }}
        onMouseLeave={(e) => {
          (e.currentTarget as HTMLDivElement).style.background = 'transparent';
        }}
      >
        <span
          style={{
            fontSize: 10,
            fontFamily: 'Roboto Mono, monospace',
            background: KC.surfaceHigh,
            color: KC.onSurfaceVariant,
            padding: '2px 6px',
            borderRadius: 3,
            flexShrink: 0,
          }}
        >
          {session.agentCode}
        </span>
        <span style={{ flex: 1, fontSize: 13, color: KC.onSurface, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {session.taskType.replace(/_/g, ' ')}
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
          <span
            style={{
              display: 'inline-block',
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: statusDotColor(session.status),
            }}
          />
          <span
            style={{
              fontFamily: 'Roboto Mono, monospace',
              fontSize: 11,
              color: statusDotColor(session.status),
            }}
          >
            {session.status}
          </span>
        </div>
        <span
          style={{
            fontFamily: 'Roboto Mono, monospace',
            fontSize: 11,
            color: KC.onSurfaceVariant,
            flexShrink: 0,
          }}
        >
          {formatRelativeTime(session.startedAt)}
        </span>
      </div>
    </Link>
  );
}

// --- Main Page ---

export function SessionsPage() {
  const { data: sessions, isLoading } = useSessions();

  if (isLoading) {
    return (
      <div style={{ fontSize: 12, color: KC.onSurfaceVariant, fontFamily: 'Roboto Mono, monospace' }}>
        Loading…
      </div>
    );
  }

  const items = sessions ?? [];
  const runningCount = items.filter((s) => s.status === 'running').length;
  const now = new Date().toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

  return (
    <>
      {/* Keyframe animations injected once */}
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.35; }
        }
      `}</style>

      <div>
        {/* Page header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
            marginBottom: 16,
          }}
        >
          <div>
            <h1
              style={{
                margin: 0,
                fontSize: 18,
                fontWeight: 500,
                color: KC.onSurface,
                lineHeight: 1.2,
              }}
            >
              Orchestrator
            </h1>
            <span
              style={{
                fontFamily: 'Roboto Mono, monospace',
                fontSize: 12,
                color: KC.onSurfaceVariant,
              }}
            >
              DAG executor · {runningCount} workflow{runningCount !== 1 ? 's' : ''} running
            </span>
          </div>
          <span
            style={{
              fontFamily: 'Roboto Mono, monospace',
              fontSize: 11,
              color: KC.onSurfaceVariant,
              marginTop: 4,
            }}
          >
            {now}
          </span>
        </div>

        {/* Two-column layout */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 300px',
            gap: 12,
            alignItems: 'start',
          }}
        >
          {/* Left column */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <DagPanel sessions={items} />
            <ExecutionLogPanel sessions={items} />

            {/* Sessions list — shown when real sessions exist */}
            {items.length > 0 && (
              <div style={{ ...glassPanel }}>
                <div style={panelHeader}>
                  <span
                    style={{
                      fontFamily: 'Roboto Mono, monospace',
                      fontSize: 10,
                      letterSpacing: '0.1em',
                      textTransform: 'uppercase',
                      color: KC.onSurfaceVariant,
                    }}
                  >
                    ALL SESSIONS
                  </span>
                </div>
                {items.map((session) => (
                  <SessionRow key={session.id} session={session} />
                ))}
              </div>
            )}
          </div>

          {/* Right column */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <PendingApprovalCard />
            <AgentRosterPanel />
          </div>
        </div>
      </div>
    </>
  );
}
