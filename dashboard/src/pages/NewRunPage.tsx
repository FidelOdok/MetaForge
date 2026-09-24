import { useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowLeft, ArrowRight, Check, Play, ShieldCheck } from 'lucide-react';

import {
  DEFAULT_DESIGN_FLOW,
  DESIGN_FLOWS,
  buildDesignFlowRequest,
  findDesignFlow,
} from '../api/endpoints/design-flows';
import { useActiveProject } from '../hooks/use-active-project';
import { useProjects } from '../hooks/use-projects';
import { useCreateRun } from '../hooks/use-runs';

export function NewRunPage() {
  const [searchParams] = useSearchParams();
  const { activeProjectId } = useActiveProject();
  const projects = useProjects();
  const createRun = useCreateRun();
  const navigate = useNavigate();

  const [projectId, setProjectId] = useState(searchParams.get('project') ?? activeProjectId ?? '');
  const [goal, setGoal] = useState('');
  const [flowId, setFlowId] = useState(DEFAULT_DESIGN_FLOW.id);
  const [reviewing, setReviewing] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);

  const flow = findDesignFlow(flowId) ?? DEFAULT_DESIGN_FLOW;
  const project = projects.data?.find((p) => p.id === projectId);
  const canContinue = !!(project && project.status !== 'archived' && goal.trim());
  const liveProjects = (projects.data ?? []).filter((p) => p.status !== 'archived');

  function goToStep(review: boolean) {
    setReviewing(review);
    createRun.reset();
    setTimeout(() => headingRef.current?.focus(), 0);
  }

  function launch() {
    createRun.mutate(
      { request: buildDesignFlowRequest(goal, projectId, flowId), start: true },
      { onSuccess: (run) => navigate(`/runs/${run.id}`) },
    );
  }

  return (
    <div className="flow-workspace">
      <Link className="text-action" to="/runs">
        <ArrowLeft size={16} />
        All runs
      </Link>
      <div className="page-heading">
        <div>
          <p className="eyebrow">INTENT TO HARDWARE</p>
          <h1 ref={headingRef} tabIndex={-1}>
            {reviewing ? 'Review your run' : 'Start a design run'}
            <span className="heading-period">.</span>
          </h1>
          <p className="page-description">
            Describe the outcome. Review the evidence at each engineering gate.
          </p>
        </div>
      </div>

      <ol className="flow-stepper" aria-label="Run setup">
        <li aria-current={reviewing ? undefined : 'step'}>
          <span>{reviewing ? <Check size={16} /> : 1}</span>
          Define the intent
        </li>
        <li aria-current={reviewing ? 'step' : undefined}>
          <span>2</span>
          Review &amp; launch
        </li>
      </ol>

      <div className="flow-columns">
        <section className="flow-panel">
          {reviewing ? (
            <div className="flow-form">
              <h2>{project?.name}</h2>
              <p className="eyebrow">YOUR INTENT</p>
              <p className="intent-summary">{goal}</p>
              <dl className="run-facts">
                <div>
                  <dt>Workflow</dt>
                  <dd>{flow.name}</dd>
                </div>
                <div>
                  <dt>Review gates</dt>
                  <dd>{flow.phases.length} human checkpoints</dd>
                </div>
                <div>
                  <dt>Execution</dt>
                  <dd>Your connected gateway</dd>
                </div>
              </dl>
              <div className="review-callout">
                <ShieldCheck size={22} />
                <p>
                  Launching starts agent and tool execution. The run pauses for your decision at
                  each phase boundary. Review generated artifacts and validation evidence before
                  approving.
                </p>
              </div>
              {createRun.isError && (
                <p className="form-error" role="alert">
                  The launch request could not be confirmed.{' '}
                  <Link to="/runs">Check the run list</Link> before retrying, to avoid starting a
                  duplicate.
                </p>
              )}
              <div className="dialog-actions">
                <button
                  type="button"
                  className="action-secondary"
                  disabled={createRun.isPending}
                  onClick={() => goToStep(false)}
                >
                  Edit intent
                </button>
                <button
                  type="button"
                  className="action-primary"
                  disabled={createRun.isPending || !canContinue}
                  onClick={launch}
                >
                  <Play size={16} />
                  {createRun.isPending ? 'Launching…' : 'Launch design run'}
                </button>
              </div>
            </div>
          ) : (
            <form
              className="flow-form"
              onSubmit={(e) => {
                e.preventDefault();
                if (canContinue) goToStep(true);
              }}
            >
              <h2>What are you building?</h2>
              <label htmlFor="run-project">Project</label>
              <select
                id="run-project"
                required
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
                disabled={projects.isLoading || projects.isError}
              >
                <option value="">Choose a project</option>
                {liveProjects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              {projects.isError ? (
                <p role="alert" className="form-error">
                  Projects could not be loaded.{' '}
                  <Link to="/settings">Check the gateway connection</Link> or{' '}
                  <button type="button" className="text-action" onClick={() => void projects.refetch()}>
                    retry
                  </button>
                  .
                </p>
              ) : projects.isLoading ? (
                <p role="status">Loading projects…</p>
              ) : projects.data?.length ? null : (
                <p>
                  Create a project in{' '}
                  <Link className="text-action" to="/projects">
                    Projects
                  </Link>{' '}
                  before starting a run.
                </p>
              )}

              <label htmlFor="run-goal">Engineering intent</label>
              <textarea
                id="run-goal"
                required
                rows={7}
                maxLength={12000}
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                aria-describedby="goal-help"
                placeholder="Design a compact actuator to lift 2 kg at a 100 mm reach, powered by 24 V. Describe the intended use, constraints and what a successful result looks like."
              />
              <p id="goal-help" className="field-help">
                Include operating conditions, loads, dimensions and budgets you know. The intent
                and needs stages precede detailed requirements.
              </p>

              <fieldset className="flow-options">
                <legend>Engineering workflow</legend>
                {DESIGN_FLOWS.map((f) => (
                  <label key={f.id} className={`flow-choice ${flowId === f.id ? 'chosen' : ''}`}>
                    <input
                      type="radio"
                      name="flow"
                      value={f.id}
                      checked={flowId === f.id}
                      onChange={() => setFlowId(f.id)}
                    />
                    <span>
                      <strong>{f.name}</strong>
                      <span>{f.description}</span>
                    </span>
                  </label>
                ))}
              </fieldset>

              <div className="dialog-actions">
                <Link className="action-secondary" to="/runs">
                  Cancel
                </Link>
                <button className="action-primary" type="submit" disabled={!canContinue}>
                  Review run
                  <ArrowRight size={17} />
                </button>
              </div>
            </form>
          )}
        </section>

        <aside className="flow-panel flow-plan">
          <p className="eyebrow">PLANNED LIFECYCLE</p>
          <h2>{flow.name}</h2>
          <ol>
            {flow.phases.map((phase, i) => (
              <li key={phase}>
                <span>{String(i + 1).padStart(2, '0')}</span>
                <div>
                  <strong>{phase}</strong>
                  <small>Human review gate</small>
                </div>
              </li>
            ))}
          </ol>
          <p className="field-help">
            This is the selected plan. Execution status and results appear after launch; available
            tools determine what can be produced.
          </p>
        </aside>
      </div>
    </div>
  );
}
