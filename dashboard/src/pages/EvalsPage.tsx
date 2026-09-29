import { useEvals } from '../hooks/use-evals';
import { EmptyState } from '../components/ui/EmptyState';
import { Badge } from '../components/ui/Badge';

/** FORGE-292 (gap G-G6): "Eval dashboard: pass rate per scenario over
 * releases" -- one real correction: this repo's own eval pipeline has no
 * concept of a CI release (evals/nightly.sh runs on a timestamp, not a
 * release tag), so this shows pass rate per scenario over recorded nightly
 * runs instead, the honest equivalent of what's actually buildable (same
 * substitution FORGE-291 made for "tokens" against a loop with no LLM
 * calls to meter). */

function rateColor(rate: number | null): string {
  if (rate === null) return 'var(--mf-c-9a9aaa)';
  if (rate >= 0.9) return 'var(--mf-c-3dd68c)';
  if (rate >= 0.5) return 'var(--mf-c-f59e0b)';
  return 'var(--mf-c-ffb4ab)';
}

function formatRate(rate: number | null): string {
  return rate === null ? '—' : `${Math.round(rate * 100)}%`;
}

function ScenarioRow({
  scenarioId,
  suite,
  run,
  runs,
  completedRate,
  avgCompleteness,
}: {
  scenarioId: string;
  suite: string;
  run: string;
  runs: number | null;
  completedRate: number | null;
  avgCompleteness: number | null;
}) {
  return (
    <tr data-testid="eval-scenario-row" style={{ borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}>
      <td className="px-3 py-2 text-sm text-on-surface">{scenarioId}</td>
      <td className="px-2 py-2 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant">
        {suite}
      </td>
      <td className="px-2 py-2 text-right font-mono text-xs" style={{ color: rateColor(completedRate) }}>
        {formatRate(completedRate)}
      </td>
      <td className="px-2 py-2 text-right font-mono text-xs text-on-surface-variant">
        {formatRate(avgCompleteness)}
      </td>
      <td className="px-2 py-2 text-right font-mono text-xs text-on-surface-variant">{runs ?? '—'}</td>
      <td className="px-2 py-2 text-right font-mono text-[10px] text-on-surface-variant">{run}</td>
    </tr>
  );
}

export function EvalsPage() {
  const { data, isLoading } = useEvals();
  const scenarios = data?.scenarios ?? [];
  const history = data?.history ?? [];

  return (
    <div>
      <div className="mb-4">
        <h1 className="text-lg font-medium leading-tight text-on-surface" style={{ margin: 0 }}>
          Evals
        </h1>
        <span className="font-mono text-xs text-on-surface-variant">
          Pass rate per scenario, over recorded nightly runs
        </span>
      </div>

      {!isLoading && scenarios.length === 0 && (
        <EmptyState
          title="No eval runs recorded yet"
          description="Run evals/run_scenarios.py or evals/design_loop_scenarios.py (see evals/README.md) to populate this page."
        />
      )}

      {scenarios.length > 0 && (
        <div
          data-testid="evals-table"
          className="rounded-lg overflow-hidden overflow-x-auto mb-4"
          style={{ background: 'var(--mf-r-30-31-38-0p85)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
        >
          <table className="w-full text-left border-collapse">
            <thead>
              <tr style={{ background: 'var(--mf-c-191b22)' }}>
                <th className="px-3 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-left" style={{ height: 32 }}>
                  Scenario
                </th>
                <th className="px-2 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-left" style={{ height: 32 }}>
                  Suite
                </th>
                <th className="px-2 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-right" style={{ height: 32 }}>
                  Pass rate
                </th>
                <th className="px-2 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-right" style={{ height: 32 }}>
                  Completeness
                </th>
                <th className="px-2 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-right" style={{ height: 32 }}>
                  Runs
                </th>
                <th className="px-2 font-mono text-[10px] uppercase tracking-widest text-on-surface-variant text-right" style={{ height: 32 }}>
                  Last run
                </th>
              </tr>
            </thead>
            <tbody>
              {scenarios.map((s) => (
                <ScenarioRow
                  key={`${s.suite}:${s.scenario_id}`}
                  scenarioId={s.scenario_id}
                  suite={s.suite}
                  run={s.run}
                  runs={s.runs}
                  completedRate={s.completed_rate}
                  avgCompleteness={s.avg_completeness}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {history.length > 0 && (
        <div>
          <span className="font-mono text-[10px] uppercase tracking-widest text-on-surface-variant">
            Recent nightly runs
          </span>
          <div className="mt-2 flex flex-wrap gap-2">
            {history.map((h) => (
              <Badge key={h.run} data-testid="eval-history-badge" title={`${h.scenarios} scenarios`}>
                {h.run} · {formatRate(h.completed_rate)}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
