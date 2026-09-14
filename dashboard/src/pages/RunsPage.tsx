import { Link } from 'react-router-dom';

import { StatusBadge } from '../components/shared/StatusBadge';
import { useRuns } from '../hooks/use-runs';
import type { HarnessRun } from '../types/run';

const KC = {
  surfaceContainer: 'rgba(30,31,38,0.85)',
  surfaceHigh: '#282a30',
  surfaceBorder: 'rgba(65,72,90,0.2)',
  onSurface: '#e2e2eb',
  onSurfaceVariant: '#9a9aaa',
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
  justifyContent: 'space-between',
};

const monoLabel: React.CSSProperties = {
  fontFamily: 'Roboto Mono, monospace',
  fontSize: 10,
  letterSpacing: '0.1em',
  textTransform: 'uppercase',
  color: KC.onSurfaceVariant,
};

function fmtTime(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString('en-GB', { hour12: false });
}

function RunRow({ run }: { run: HarnessRun }) {
  const goal = typeof run.request.goal === 'string' ? run.request.goal : '(no goal)';
  return (
    <Link to={`/runs/${run.id}`} style={{ textDecoration: 'none', display: 'block' }}>
      <div
        style={{
          height: 40,
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          padding: '0 16px',
          borderBottom: '1px solid rgba(65,72,90,0.08)',
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
          {run.id}
        </span>
        <span
          style={{
            flex: 1,
            fontSize: 13,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {goal}
        </span>
        <StatusBadge status={run.status} dot />
        <span style={{ fontFamily: 'Roboto Mono, monospace', fontSize: 11, color: KC.onSurfaceVariant, flexShrink: 0 }}>
          {fmtTime(run.updatedAt)}
        </span>
      </div>
    </Link>
  );
}

export function RunsPage() {
  const { data: runs, isLoading } = useRuns();
  const items = runs ?? [];

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ margin: 0, fontSize: 18, fontWeight: 500, color: KC.onSurface, lineHeight: 1.2 }}>
          Harness Runs
        </h1>
        <span style={{ fontFamily: 'Roboto Mono, monospace', fontSize: 12, color: KC.onSurfaceVariant }}>
          {items.length} run{items.length !== 1 ? 's' : ''}
        </span>
      </div>

      <div style={{ ...glassPanel }}>
        <div style={panelHeader}>
          <span style={monoLabel}>ALL RUNS</span>
        </div>
        {isLoading ? (
          <div style={{ padding: 16, fontSize: 12, color: KC.onSurfaceVariant, fontFamily: 'Roboto Mono, monospace' }}>
            Loading…
          </div>
        ) : items.length === 0 ? (
          <div style={{ padding: 16, fontSize: 13, color: KC.onSurfaceVariant }}>
            No runs yet. Runs are launched by driving the harness via the CLI (`forge chat`) or
            an MCP client with a design flow — this page observes them live.
          </div>
        ) : (
          items.map((run) => <RunRow key={run.id} run={run} />)
        )}
      </div>
    </div>
  );
}
