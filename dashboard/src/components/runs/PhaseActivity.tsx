/**
 * What one phase actually did (FORGE-396).
 *
 * The lane beside the flow graph. Selecting a phase shows its objective, the
 * events the workflow recorded for it, and what it committed.
 *
 * **On honesty about coverage.** The ticket asks this lane to show tool
 * calls, decisions, evidence, iterations and token cost as well. Those come
 * from session capture and twin commits, which the workflow's own event log
 * does not carry — so rather than render an empty "Tool calls" heading that
 * reads as "this phase made none", the panel names what is not wired and
 * why. An empty section and an unwired one look identical, and only one of
 * them is information.
 */
import type { FlowPhaseState, FlowRunEvent } from '../../types/run-flow';

export interface PhaseActivityProps {
  phase: FlowPhaseState | null;
  events: FlowRunEvent[];
  live: boolean;
}

function timeOf(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleTimeString();
}

export function PhaseActivity({ phase, events, live }: PhaseActivityProps) {
  if (!phase) {
    return (
      <aside className="glass rounded" data-testid="phase-activity">
        <p className="eyebrow">PHASE ACTIVITY</p>
        <p className="field-help">Select a phase on the graph.</p>
      </aside>
    );
  }

  const phaseEvents = events.filter((e) => e.phase === phase.id);

  return (
    <aside className="glass rounded" data-testid="phase-activity">
      <p className="eyebrow">PHASE ACTIVITY — {phase.title.toUpperCase()}</p>

      {!live && (
        <p className="field-help" role="status">
          The engine could not be queried, so this phase&apos;s status is unknown — not
          idle.
        </p>
      )}

      {phase.summary && <p>{phase.summary}</p>}

      <p className="eyebrow">TIMELINE</p>
      {phaseEvents.length === 0 ? (
        <p className="field-help">
          {live
            ? 'No events recorded for this phase yet.'
            : 'No events could be read.'}
        </p>
      ) : (
        <ol aria-label="Phase timeline">
          {phaseEvents.map((e, i) => (
            <li key={`${e.event}-${i}`}>
              <span className="mono-value">{timeOf(e.at)}</span>
              <strong>{e.event}</strong>
              {e.detail && <span>{e.detail}</span>}
            </li>
          ))}
        </ol>
      )}

      <p className="eyebrow">COMMITTED</p>
      {phase.artifacts.length === 0 ? (
        <p className="field-help">
          {/* "no data" and "pass" must not render the same (FORGE-361). A
              phase that committed nothing says so; it does not simply show
              an empty list that reads as tidy. */}
          Nothing committed to the twin by this phase.
        </p>
      ) : (
        <ul aria-label="Committed work products">
          {phase.artifacts.map((a) => (
            <li key={a} className="mono-value">
              {a}
            </li>
          ))}
        </ul>
      )}

      <p className="field-help" data-testid="coverage-note">
        Tool calls, decisions, evidence and token cost are not shown here yet: they come
        from session capture and twin commits, which this run&apos;s event log does not
        carry. An empty section would read as &quot;this phase made none&quot;.
      </p>
    </aside>
  );
}
