import { useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, ArrowUpRight, RefreshCw, ShieldCheck, X } from 'lucide-react';

import { StatusBadge } from '../components/shared/StatusBadge';
import { FlowGraph } from '../components/runs/FlowGraph';
import { PhaseActivity } from '../components/runs/PhaseActivity';
import { useDesignFlows } from '../hooks/use-design-flows';
import { useFlowState } from '../hooks/use-flow-state';
import { useRun, useSubmitApproval } from '../hooks/use-runs';
import type { ApprovalDecision } from '../types/run';

type PhaseResult = Record<string, unknown>;

function toIso(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toISOString();
}

export function RunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const runQuery = useRun(id);
  const designFlows = useDesignFlows();
  const [selectedPhaseId, setSelectedPhaseId] = useState<string | null>(null);
  // Only poll while the run can still change. A finished run polling
  // forever is a request every two seconds forever, per open tab.
  const runActive = !['completed', 'failed', 'rejected', 'canceled'].includes(
    String(runQuery.data?.status ?? ''),
  );
  const flowState = useFlowState(id, runActive);
  const approval = useSubmitApproval();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [decision, setDecision] = useState<ApprovalDecision>('approve');
  const run = runQuery.data;

  function openDecision(next: ApprovalDecision) {
    setDecision(next);
    approval.reset();
    // jsdom lacks showModal; fall back to the open attribute.
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
  }

  function closeDecision() {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (typeof dialog.close === 'function') dialog.close();
    else dialog.removeAttribute('open');
  }

  if (runQuery.isLoading) {
    return (
      <div className="workspace-empty" role="status">
        Loading run…
      </div>
    );
  }
  if (runQuery.isError) {
    return (
      <div className="workspace-empty" role="alert">
        <h1>Run could not be loaded</h1>
        <p>Your gateway may be unavailable. The run’s status is unknown.</p>
        <button type="button" className="action-secondary" onClick={() => void runQuery.refetch()}>
          Try again
        </button>
        <Link to="/settings" className="text-action">
          Connection settings
        </Link>
      </div>
    );
  }
  if (!run) {
    return (
      <div className="workspace-empty">
        <h1>Run not found</h1>
        <Link className="text-action" to="/runs">
          Back to runs
        </Link>
      </div>
    );
  }

  const flow = designFlows.data?.flows.find((f) => f.id === run.request.flow);
  const projectId = typeof run.request.project_id === 'string' ? run.request.project_id : undefined;
  const rawPhases = run.result?.phases;
  const phases: PhaseResult[] = Array.isArray(rawPhases)
    ? rawPhases.filter((p): p is PhaseResult => !!p && typeof p === 'object')
    : [];
  const awaiting = run.status === 'awaiting_approval';

  return (
    <div className="flow-workspace">
      <Link to="/runs" className="text-action">
        <ArrowLeft size={16} />
        All runs
      </Link>
      <div className="page-heading">
        <div>
          <p className="eyebrow">EXECUTION &amp; EVIDENCE</p>
          <h1>
            Design run<span className="heading-period">.</span>
          </h1>
          <p className="page-description">{String(run.request.goal ?? 'Run details')}</p>
        </div>
        <div className="heading-actions">
          <StatusBadge status={run.status} />
          <button
            type="button"
            className="action-secondary"
            disabled={runQuery.isFetching}
            onClick={() => void runQuery.refetch()}
          >
            <RefreshCw size={16} />
            Refresh
          </button>
        </div>
      </div>

      {/* FORGE-396: phases and gates as a graph, with the selected phase's
          activity beside it. Rendered whenever the run is a design flow --
          including when the engine cannot be queried, because "unknown"
          is information and a missing panel is not. */}
      {flowState.data && flowState.data.phases.length > 0 && (
        <div className="flow-run-view">
          {!flowState.data.live && (
            <p role="status" className="field-help" data-testid="flow-state-degraded">
              {flowState.data.detail}
            </p>
          )}
          <FlowGraph
            phases={flowState.data.phases}
            selectedPhaseId={selectedPhaseId}
            onSelectPhase={setSelectedPhaseId}
            canApprove={awaiting && !approval.isPending}
            onApprove={(decision) => openDecision(decision)}
          />
          <PhaseActivity
            phase={
              flowState.data.phases.find((p) => p.id === selectedPhaseId) ??
              flowState.data.phases.find((p) => p.id === flowState.data?.currentPhase) ??
              null
            }
            events={flowState.data.events}
            live={flowState.data.live}
          />
        </div>
      )}

      <dl className="run-facts flow-panel">
        <div>
          <dt>Run ID</dt>
          <dd className="mono-value">{run.id}</dd>
        </div>
        <div>
          <dt>Workflow</dt>
          <dd>{flow?.label ?? String(run.request.flow ?? 'Custom run')}</dd>
        </div>
        <div>
          <dt>Last updated</dt>
          <dd>
            <time dateTime={toIso(run.updatedAt)}>
              {new Date(run.updatedAt * 1000).toLocaleString()}
            </time>
          </dd>
        </div>
      </dl>

      {awaiting && (
        <section className="gate-review flow-panel" aria-labelledby="gate-title">
          <ShieldCheck size={28} />
          <div>
            <h2 id="gate-title">Your review is required</h2>
            <p className="intent-summary">
              {run.approvalReason || 'Review this run’s evidence before continuing.'}
            </p>
            {projectId && (
              <Link className="text-action" to={`/projects/${encodeURIComponent(projectId)}`}>
                Inspect project artifacts
                <ArrowUpRight size={16} />
              </Link>
            )}
            <div className="heading-actions">
              <button type="button" className="action-primary" onClick={() => openDecision('approve')}>
                Review approval
              </button>
              <button type="button" className="action-secondary" onClick={() => openDecision('reject')}>
                Reject run
              </button>
            </div>
          </div>
        </section>
      )}

      {run.error && (
        <section className="flow-panel form-error" role="alert">
          <h2>Execution error</h2>
          <p>{run.error}</p>
        </section>
      )}

      <div className="flow-columns">
        <section className="flow-panel">
          <h2>Evidence &amp; results</h2>
          {phases.length ? (
            <div className="phase-results">
              {phases.map((phase, i) => (
                <article key={String(phase.id ?? i)}>
                  <div className="section-heading">
                    <h3>{String(phase.title ?? phase.id ?? 'Phase')}</h3>
                    <span className="status-tag">{String(phase.status ?? 'Recorded')}</span>
                  </div>
                  <p className="intent-summary">{String(phase.summary ?? 'No summary supplied.')}</p>
                  {phase.artifacts != null && (
                    <details>
                      <summary>Recorded artifacts</summary>
                      <pre>{JSON.stringify(phase.artifacts, null, 2)}</pre>
                    </details>
                  )}
                </article>
              ))}
            </div>
          ) : (
            <div className="context-empty">
              <h3>No phase results returned yet</h3>
              <p>
                The gateway publishes the phase summary when the flow completes. Inspect project
                artifacts for available work products during execution.
              </p>
              {projectId && (
                <Link className="text-action" to={`/projects/${encodeURIComponent(projectId)}`}>
                  Open project
                  <ArrowUpRight size={16} />
                </Link>
              )}
            </div>
          )}
          <details className="run-raw">
            <summary>Run request</summary>
            <pre>{JSON.stringify(run.request, null, 2)}</pre>
          </details>
          {run.result && (
            <details className="run-raw">
              <summary>Complete gateway result</summary>
              <pre>{JSON.stringify(run.result, null, 2)}</pre>
            </details>
          )}
        </section>

        <aside className="flow-panel flow-plan">
          <h2>Run history</h2>
          <p className="field-help">Status transitions reported by the gateway.</p>
          <ol>
            {run.history.map((status, i) => (
              <li key={i}>
                <span>{String(i + 1).padStart(2, '0')}</span>
                <div>
                  <strong>{status.replace(/_/g, ' ')}</strong>
                  {i === run.history.length - 1 && <small>Latest recorded state</small>}
                </div>
              </li>
            ))}
          </ol>
        </aside>
      </div>

      <dialog ref={dialogRef} className="project-dialog" aria-labelledby="decision-title">
        <div className="flow-form">
          <div className="section-heading">
            <h2 id="decision-title">
              {decision === 'approve' ? 'Approve this gate?' : 'Reject this run?'}
            </h2>
            <button
              type="button"
              className="icon-control"
              aria-label="Close decision"
              disabled={approval.isPending}
              onClick={closeDecision}
            >
              <X size={20} />
            </button>
          </div>
          <p>
            {decision === 'approve'
              ? 'Approval resumes execution. Confirm that you have reviewed the available evidence and accept this gate.'
              : 'Rejection ends this run. Its recorded history remains available for review.'}
          </p>
          {approval.isError && (
            <p className="form-error" role="alert">
              The decision could not be confirmed. Refresh the run to check its current state before
              retrying.
            </p>
          )}
          {!awaiting && (
            <p role="status">
              This run is no longer waiting for approval. Close this dialog to view its current
              state.
            </p>
          )}
          <div className="dialog-actions">
            <button
              type="button"
              className="action-secondary"
              disabled={approval.isPending}
              onClick={closeDecision}
            >
              Cancel
            </button>
            <button
              type="button"
              className="action-primary"
              disabled={approval.isPending || !awaiting}
              onClick={() =>
                approval.mutate({ id: run.id, decision }, { onSuccess: () => closeDecision() })
              }
            >
              {approval.isPending
                ? 'Submitting…'
                : decision === 'approve'
                  ? 'Approve & continue'
                  : 'Confirm rejection'}
            </button>
          </div>
        </div>
      </dialog>
    </div>
  );
}
