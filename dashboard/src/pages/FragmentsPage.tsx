import { PageHeader } from '../components/ui/PageHeader';

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
      <PageHeader
        back={{ to: '/runs/new', label: 'New run' }}
        title="Domain fragments"
        meta="composable workflows"
        description="Each fragment adds one domain's tasks to a workflow. A task's dependencies come from the items it reads and writes, and the gate of its stage checks it."
      />

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

/** `design_value:wing_area_m2` reads as `wing_area_m2`; every item here is a design value. */
export function itemName(ref: string): string {
  return ref.replace(/^design_value:/, '');
}

// One task, closed to a line by default: a fragment has a dozen tasks, and
// all of them open made the page ten screens long.
function TaskRow({ task }: { task: DesignFragmentTask }) {
  return (
    <li>
      <details className="fragment-task">
        <summary>
          <strong>{task.title}</strong>
          <span className="field-help">
            {' '}
            · reads {task.inputs.length}, writes {task.outputs.length}
            {task.checks.length > 0 ? `, ${task.checks.length} check${task.checks.length === 1 ? '' : 's'}` : ''}
          </span>
        </summary>
        {/* Each label and value share a wrapper, so the grid keeps them together. */}
        <dl className="run-facts">
          <div>
            <dt>Reads</dt>
            <dd>{task.inputs.length ? task.inputs.map(itemName).join(', ') : 'nothing from other tasks'}</dd>
          </div>
          <div>
            <dt>Writes</dt>
            <dd>{task.outputs.map(itemName).join(', ')}</dd>
          </div>
          {task.checks.length > 0 && (
            <div>
              <dt>Checks</dt>
              <dd>{task.checks.join('; ')}</dd>
            </div>
          )}
          {task.capabilities.length > 0 && (
            <div>
              <dt>Tools</dt>
              <dd>{task.capabilities.join(', ')}</dd>
            </div>
          )}
        </dl>
      </details>
    </li>
  );
}
