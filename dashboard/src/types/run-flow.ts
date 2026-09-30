/** Live design-flow run state, from `GET /v1/runs/{id}/flow-state` (FORGE-396). */

export interface FlowPhaseState {
  id: string;
  title: string;
  /** pending | running | awaiting_gate | passed | failed | rejected | unknown */
  status: string;
  summary: string;
  artifacts: string[];
  gate: string | null;
  disciplines: string[];
}

export interface FlowRunEvent {
  event: string;
  phase: string | null;
  detail: string;
  at: string;
}

export interface FlowRunState {
  runId: string;
  status: string;
  currentPhase: string | null;
  awaitingGate: string | null;
  phases: FlowPhaseState[];
  events: FlowRunEvent[];
  /**
   * False when the engine could not be queried. The view must render that as
   * "unknown", never as "nothing is happening" — a worker that is down and a
   * flow that has not started look identical otherwise, and one is an outage.
   */
  live: boolean;
  detail: string;
}
