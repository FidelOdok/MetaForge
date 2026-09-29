/** FORGE-292 (gap G-G6): "pass rate per scenario over releases" -- this
 * repo's own eval pipeline has no concept of a CI release, only timestamped
 * nightly runs (see api_gateway/evals/routes.py's own docstring), so
 * `run` here is a timestamp directory name, not a release tag. */

export interface EvalScenarioRow {
  scenario_id: string;
  suite: string;
  run: string;
  runs: number | null;
  completed_rate: number | null;
  avg_completeness: number | null;
}

export interface EvalHistoryRow {
  run: string;
  suite: string;
  scenarios: number;
  completed_rate: number;
}

export interface EvalsReport {
  scenarios: EvalScenarioRow[];
  history: EvalHistoryRow[];
}
