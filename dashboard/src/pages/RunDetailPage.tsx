import { Link, useParams } from 'react-router-dom';

import { StatusBadge } from '../components/shared/StatusBadge';
import { useRun, useSubmitApproval } from '../hooks/use-runs';
import type { RunStatus } from '../types/run';

const KC = {
  surfaceContainer: 'rgba(30,31,38,0.85)',
  surfaceHigh: '#282a30',
  surfaceBorder: 'rgba(65,72,90,0.2)',
  onSurface: '#e2e2eb',
  onSurfaceVariant: '#9a9aaa',
  running: '#e67e22',
  done: '#3dd68c',
  error: '#ffb4ab',
  logBg: '#0a0b10',
} as const;

const glassPanel: React.CSSProperties = {
  background: KC.surfaceContainer,
  backdropFilter: 'blur(16px)',
  WebkitBackdropFilter: 'blur(16px)',
  borderRadius: 4,
  border: `1px solid ${KC.surfaceBorder}`,
};

const panelHeader: React.CSSProperties = {
  height: 36,
  borderBottom: `1px solid ${KC.surfaceBorder}`,
  padding: '0 16px',
  display: 'flex',
  alignItems: 'center',
};

const monoLabel: React.CSSProperties = {
  fontFamily: 'Roboto Mono, monospace',
  fontSize: 10,
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  color: KC.onSurfaceVariant,
};

function dotColor(status: RunStatus): string {
  if (status === 'completed') return KC.done;
  if (status === 'failed' || status === 'rejected') return KC.error;
  if (status === 'running' || status === 'awaiting_approval') return KC.running;
  return KC.onSurfaceVariant;
}

export function RunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: run, isLoading } = useRun(id);
  const approval = useSubmitApproval();

  if (isLoading) {
    return (
      <div style={{ fontSize: 12, color: KC.onSurfaceVariant, fontFamily: 'Roboto Mono, monospace' }}>
        Loading…
      </div>
    );
  }
  if (!run) {
    return (
      <div style={{ color: KC.onSurfaceVariant }}>
        Run not found. <Link to="/runs" style={{ color: KC.running }}>Back to runs</Link>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Link to="/runs" style={{ color: KC.onSurfaceVariant, textDecoration: 'none', fontSize: 20, lineHeight: 1 }}>
          <span className="material-symbols-outlined">arrow_back</span>
        </Link>
        <h1 style={{ margin: 0, fontSize: 18, fontWeight: 500, color: KC.onSurface, fontFamily: 'Roboto Mono, monospace' }}>
          {run.id}
        </h1>
        <StatusBadge status={run.status} />
      </div>

      {/* Approval actions when paused */}
      {run.status === 'awaiting_approval' && (
        <div style={{ ...glassPanel, padding: '12px 16px' }}>
          <div style={{ fontSize: 13, color: KC.onSurface, marginBottom: 10 }}>
            {run.approvalReason || 'This run is awaiting approval to continue.'}
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={() => approval.mutate({ id: run.id, decision: 'approve' })}
              disabled={approval.isPending}
              style={{ height: 30, padding: '0 14px', background: KC.running, border: 'none', borderRadius: 4, cursor: 'pointer', fontSize: 11, color: '#fff', fontFamily: 'inherit' }}
            >
              Approve
            </button>
            <button
              onClick={() => approval.mutate({ id: run.id, decision: 'reject' })}
              disabled={approval.isPending}
              style={{ height: 30, padding: '0 14px', background: 'rgba(65,72,90,0.35)', border: `1px solid ${KC.surfaceBorder}`, borderRadius: 4, cursor: 'pointer', fontSize: 11, color: KC.onSurface, fontFamily: 'inherit' }}
            >
              Reject
            </button>
          </div>
        </div>
      )}

      {/* Error */}
      {run.error && (
        <div style={{ ...glassPanel }}>
          <div style={panelHeader}><span style={monoLabel}>ERROR</span></div>
          <div style={{ padding: '12px 16px', fontSize: 12, color: KC.error, fontFamily: 'Roboto Mono, monospace' }}>
            {run.error}
          </div>
        </div>
      )}

      {/* Request */}
      <div style={{ ...glassPanel }}>
        <div style={panelHeader}><span style={monoLabel}>REQUEST</span></div>
        <pre style={{ margin: 0, padding: '12px 16px', background: KC.logBg, fontSize: 11, fontFamily: 'Roboto Mono, monospace', color: KC.onSurface, overflowX: 'auto', borderRadius: '0 0 4px 4px' }}>
          {JSON.stringify(run.request, null, 2)}
        </pre>
      </div>

      {/* Result */}
      {run.result && (
        <div style={{ ...glassPanel }}>
          <div style={panelHeader}><span style={monoLabel}>RESULT</span></div>
          <pre style={{ margin: 0, padding: '12px 16px', background: KC.logBg, fontSize: 11, fontFamily: 'Roboto Mono, monospace', color: KC.done, overflowX: 'auto', borderRadius: '0 0 4px 4px' }}>
            {JSON.stringify(run.result, null, 2)}
          </pre>
        </div>
      )}

      {/* Lifecycle history */}
      <div style={{ ...glassPanel }}>
        <div style={panelHeader}><span style={monoLabel}>LIFECYCLE</span></div>
        <div style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 8 }}>
          {run.history.map((status, idx) => (
            <div key={`${status}-${idx}`} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: dotColor(status), flexShrink: 0 }} />
              <span style={{ fontFamily: 'Roboto Mono, monospace', fontSize: 12, color: KC.onSurface }}>{status}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
