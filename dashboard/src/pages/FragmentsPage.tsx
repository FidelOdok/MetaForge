import { Link } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';

import { useDesignFragments } from '../hooks/use-design-fragments';
import type { DesignFragment, DesignFragmentTask } from '../types/design-flows';

/**
 * The domain fragments a proposal can compose into a workflow (composable flows).
 *
 * Read-only on purpose: a fragment joins a flow through a proposal's
 * `add_fragment` operation, which a person approves, never from here. What
 * this page answers is "what would thermal add to my flow": each task's
 * stage, what it reads and writes, how it is checked and which tools it needs.
 */
export function FragmentsPage() {
  const catalog = useDesignFragments();
  const fragments = catalog.data?.fragments ?? [];

  return (
    <div className="flow-workspace">
      <Link className="text-action" to="/runs/new">
        <ArrowLeft size={16} />
        New run
      </Link>
      <div className="page-heading">
        <div>
          <p className="eyebrow">COMPOSABLE WORKFLOWS</p>
          <h1>
            Domain fragments<span className="heading-period">.</span>
          </h1>
          <p className="page-description">
            Each fragment adds one domain&apos;s tasks to a workflow. A task&apos;s dependencies
            come from the items it reads and writes, and the gate of its stage checks it.
          </p>
        </div>
      </div>

      {catalog.isLoading && <p className="field-help">Loading fragments…</p>}
      {catalog.isError && (
        <p className="form-error" role="alert">
          Could not load the fragment registry from the gateway.
        </p>
      )}
      {catalog.isSuccess && fragments.length === 0 && (
        <p className="field-help">The gateway serves no domain fragments.</p>
      )}

      {fragments.map((fragment) => (
        <FragmentPanel key={fragment.id} fragment={fragment} />
      ))}
    </div>
  );
}

function FragmentPanel({ fragment }: { fragment: DesignFragment }) {
  const stages = Array.from(new Set(fragment.tasks.map((t) => t.stage)));
  return (
    <section className="flow-panel" aria-labelledby={`fragment-${fragment.id}`}>
      <h2 id={`fragment-${fragment.id}`}>
        {fragment.label} <span className="field-help">{fragment.id} · v{fragment.version}</span>
      </h2>
      <p>{fragment.description}</p>
      {fragment.requires.length > 0 && (
        <p className="field-help">Needs {fragment.requires.join(', ')} composed alongside it.</p>
      )}
      {stages.map((stage) => (
        <div key={stage}>
          <h3>Reviewed at the {stage} gate</h3>
          <ul>
            {fragment.tasks
              .filter((t) => t.stage === stage)
              .map((task) => (
                <TaskRow key={task.id} task={task} />
              ))}
          </ul>
        </div>
      ))}
      {fragment.blocks.map((block) => (
        <p key={block.id} className="review-callout">
          Coupled loop <strong>{block.id}</strong>: {block.members.join(', ')} are solved together
          until {block.solvesFor.join(', ')} change by less than{' '}
          {(block.tolerance * 100).toFixed(1)}% (at most {block.maxIterations} passes).
        </p>
      ))}
    </section>
  );
}

function TaskRow({ task }: { task: DesignFragmentTask }) {
  return (
    <li>
      <strong>{task.title}</strong>
      <dl className="run-facts">
        <dt>Reads</dt>
        <dd>{task.inputs.length ? task.inputs.join(', ') : 'nothing from other tasks'}</dd>
        <dt>Writes</dt>
        <dd>{task.outputs.join(', ')}</dd>
        {task.checks.length > 0 && (
          <>
            <dt>Checks</dt>
            <dd>{task.checks.join('; ')}</dd>
          </>
        )}
        {task.capabilities.length > 0 && (
          <>
            <dt>Tools</dt>
            <dd>{task.capabilities.join(', ')}</dd>
          </>
        )}
      </dl>
    </li>
  );
}
