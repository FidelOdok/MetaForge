/**
 * What one phase actually did (FORGE-396).
 *
 * The lane beside the flow graph. Selecting a phase shows its objective, the
 * events the workflow recorded for it, and what it committed.
 *
 * **Tool calls (FORGE-645).** They come from the run stream, not from the
 * workflow's event log, so they are shown only while that stream is
 * connected. When it is not, the section says so instead of rendering an
 * empty list, which would read as "this phase made none".
 *
 * **On honesty about coverage.** The ticket asks this lane to show decisions,
 * evidence, iterations and token cost as well. Those come from session
 * capture and twin commits, which the workflow's own event log does not
 * carry, so the panel names what is not wired and why. An empty section and
 * an unwired one look identical, and only one of them is information.
 */
import type { ToolCall } from '../../hooks/use-run-tool-events';
import type { FlowPhaseState, FlowRunEvent } from '../../types/run-flow';

export interface PhaseActivityProps {
  phase: FlowPhaseState | null;
  events: FlowRunEvent[];
  live: boolean;
  /** Tool calls from the run stream. Omitted where nothing streams them. */
  toolCalls?: ToolCall[];
  /** Whether the run stream is open right now. */
  toolStreamConnected?: boolean;
}

function toolLabel(call: ToolCall): string {
  if (call.status === 'running') return 'running';
  const took = call.durationMs === null ? '' : ` in ${(call.durationMs / 1000).toFixed(1)}s`;
  return `${call.status}${took}`;
}

function timeOf(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleTimeString();
}

export function PhaseActivity({
  phase,
  events,
  live,
  toolCalls,
  toolStreamConnected = false,
}: PhaseActivityProps) {
  if (!phase) {
    return (
      <aside className="glass rounded" data-testid="phase-activity">
        <p className="eyebrow">PHASE ACTIVITY</p>
        <p className="field-help">Select a phase on the graph.</p>
      </aside>
    );
  }

  const phaseEvents = events.filter((e) => e.phase === phase.id);
  const phaseCalls = (toolCalls ?? []).filter((c) => c.phase === phase.id);

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

      {toolCalls !== undefined && (
        <>
          <p className="eyebrow">TOOL CALLS</p>
          {phaseCalls.length > 0 ? (
            <ol aria-label="Tool calls" data-testid="tool-calls">
              {phaseCalls.map((c) => (
                <li key={c.callId} data-status={c.status}>
                  <strong className="mono-value">{c.tool}</strong>{' '}
                  <span>{toolLabel(c)}</span>
                </li>
              ))}
            </ol>
          ) : (
            <p className="field-help" role="status" data-testid="tool-calls-empty">
              {toolStreamConnected
                ? 'No tool calls from this phase yet.'
                : 'Live tool calls are not connected, so none can be shown. This is not the same as none made.'}
            </p>
          )}
        </>
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
        Decisions, evidence and token cost are not shown here yet: they come from session
        capture and twin commits, which this run&apos;s event log does not carry. An empty
        section would read as &quot;this phase made none&quot;.
      </p>
    </aside>
  );
}
