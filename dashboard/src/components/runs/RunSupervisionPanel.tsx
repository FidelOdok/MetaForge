import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import apiClient from '../../api/client';

/**
 * FORGE-739: the run supervisor on the run page.
 *
 * The supervisor drives a design run to a verified result: gates are reviewed
 * and decided within the approval policy (never by the supervisor itself), a
 * failed or unverified run restarts as a new linked run with an amended brief,
 * and anything only a person can fix stops it with the reason.
 */

export interface Supervision {
  active: boolean;
  status: string;
  reason: string;
  chain: string[];
  next_run?: string;
  budgets: Record<string, number>;
  counters: { actions?: number; restarts?: number };
  log: { at: string; run_id: string; action: string; detail: string }[];
}

const STATUS_TEXT: Record<string, string> = {
  watching: 'Watching the run',
  waiting_for_person: 'Waiting for a person at a gate',
  handed_over: 'Continued in a new run',
  verified: 'Verified: the design meets its requirements',
  stopped: 'Stopped',
};

async function getSupervision(runId: string): Promise<Supervision | null> {
  try {
    const { data } = await apiClient.get<Supervision>(`/runs/${runId}/supervision`);
    return data;
  } catch (err) {
    if ((err as { response?: { status?: number } }).response?.status === 404) return null;
    throw err;
  }
}

export function RunSupervisionPanel({ runId, ended }: { runId: string; ended: boolean }) {
  const qc = useQueryClient();
  const key = ['run-supervision', runId] as const;
  const { data: sup, isLoading } = useQuery({
    queryKey: key,
    queryFn: () => getSupervision(runId),
    refetchInterval: 10_000,
  });
  const act = useMutation({
    mutationFn: async (action: 'start' | 'stop') => {
      const path = action === 'start' ? `/runs/${runId}/supervise` : `/runs/${runId}/supervise/stop`;
      const body = action === 'start' ? {} : { reason: 'stopped from the dashboard' };
      const { data } = await apiClient.post<Supervision>(path, body, {
        headers: { 'X-MetaForge-Surface': 'dashboard' },
      });
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(key, data);
      void qc.invalidateQueries({ queryKey: ['runs'] });
    },
  });

  if (isLoading) return null;

  return (
    <section className="flow-panel" aria-labelledby="supervisor-title" data-testid="run-supervision">
      <h2 id="supervisor-title">Run supervisor</h2>
      {!sup ? (
        <>
          <p style={{ margin: 0 }}>
            Not supervised. The supervisor drives this run to a verified result: it lets the approval
            policy decide gates, restarts a failed or unverified run with what it learned, and stops for
            a person on anything it cannot fix.
          </p>
          {!ended && (
            <button type="button" className="action-primary" onClick={() => act.mutate('start')}>
              Supervise this run
            </button>
          )}
        </>
      ) : (
        <>
          <p style={{ margin: 0 }} data-testid="supervision-status">
            <strong>{STATUS_TEXT[sup.status] ?? sup.status}</strong>
            {sup.reason ? `: ${sup.reason}` : ''}
          </p>
          {sup.next_run && (
            <p style={{ margin: 0 }}>
              Continued in <Link to={`/runs/${sup.next_run}`}>{sup.next_run}</Link>
            </p>
          )}
          <p style={{ margin: 0, fontSize: 12 }}>
            Restarts {sup.counters.restarts ?? 0} of {sup.budgets.max_restarts} · actions{' '}
            {sup.counters.actions ?? 0} of {sup.budgets.max_actions} · runs in the chain{' '}
            {sup.chain.length}
          </p>
          <ol style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
            {sup.log.slice(-8).map((entry, i) => (
              <li key={i}>
                <span className="font-mono">{entry.action}</span> {entry.detail}
              </li>
            ))}
          </ol>
          {sup.active && (
            <button type="button" className="action-secondary" onClick={() => act.mutate('stop')}>
              Stop supervising
            </button>
          )}
        </>
      )}
      {act.isError && <p role="alert">{(act.error as Error).message}</p>}
    </section>
  );
}
