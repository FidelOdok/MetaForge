import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowUpRight, Play, RefreshCw, Search } from 'lucide-react';

import { StatusBadge } from '../components/shared/StatusBadge';
import { useRuns } from '../hooks/use-runs';
import type { RunStatus } from '../types/run';

const RUN_STATUSES: RunStatus[] = [
  'running',
  'queued',
  'awaiting_approval',
  'completed',
  'failed',
  'rejected',
  'canceled',
];

function toIso(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toISOString();
}

function toLocal(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleString();
}

export function RunsPage() {
  const runs = useRuns();
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<'all' | RunStatus>('all');

  const filtered = (runs.data ?? [])
    .filter(
      (r) =>
        (status === 'all' || r.status === status) &&
        `${r.id} ${String(r.request.goal ?? '')}`.toLowerCase().includes(query.toLowerCase()),
    )
    .sort((a, b) => b.updatedAt - a.updatedAt);
  const filtering = !!query || status !== 'all';

  return (
    <div>
      <div className="page-heading">
        <div>
          <p className="eyebrow">EXECUTION &amp; EVIDENCE</p>
          <h1>
            Runs<span className="heading-period">.</span>
          </h1>
          <p className="page-description">
            Follow execution, inspect outcomes, and review approval gates.
          </p>
        </div>
        <div className="heading-actions">
          <Link className="action-primary" to="/runs/new">
            <Play size={16} />
            New design run
          </Link>
          <button
            type="button"
            className="action-secondary"
            onClick={() => void runs.refetch()}
            disabled={runs.isFetching}
          >
            <RefreshCw size={16} />
            Refresh
          </button>
        </div>
      </div>

      <div className="project-toolbar">
        <label className="project-search">
          <Search size={18} />
          <input
            aria-label="Search runs"
            placeholder="Search by goal or run ID…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <select
          aria-label="Run status"
          value={status}
          onChange={(e) => setStatus(e.target.value as 'all' | RunStatus)}
        >
          <option value="all">All statuses</option>
          {RUN_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, ' ')}
            </option>
          ))}
        </select>
      </div>

      {runs.isLoading ? (
        <div className="workspace-empty" role="status">
          Loading runs…
        </div>
      ) : runs.isError ? (
        <div className="workspace-empty" role="alert">
          <h2>Runs could not be loaded</h2>
          <p>Check your gateway connection and try again.</p>
          <Link className="text-action" to="/settings">
            Connection settings
            <ArrowUpRight size={16} />
          </Link>
        </div>
      ) : filtered.length ? (
        <div className="runs-table-wrap">
          <table className="runs-table">
            <thead>
              <tr>
                <th scope="col">Goal / Run</th>
                <th scope="col">Status</th>
                <th scope="col">Last updated</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => (
                <tr key={r.id}>
                  <td>
                    <Link to={`/runs/${r.id}`}>
                      <strong>{String(r.request.goal ?? 'Untitled run')}</strong>
                      <small>{r.id}</small>
                    </Link>
                  </td>
                  <td>
                    <StatusBadge status={r.status} dot />
                  </td>
                  <td>
                    <time dateTime={toIso(r.updatedAt)}>{toLocal(r.updatedAt)}</time>
                  </td>
                  <td>
                    <Link className="text-action" to={`/runs/${r.id}`}>
                      Inspect
                      <ArrowUpRight size={15} />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="workspace-empty">
          <Play size={34} />
          <h2>{filtering ? 'No matching runs' : 'No runs yet'}</h2>
          <p>
            {filtering
              ? 'Try another search or status.'
              : 'Start a design run here, or follow runs created through your CLI or MCP client.'}
          </p>
        </div>
      )}
    </div>
  );
}
