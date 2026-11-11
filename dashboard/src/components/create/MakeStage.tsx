import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { CircleAlert, CircleCheck, CircleDashed } from 'lucide-react';
import { dfmKeys, getReleaseChecklist, type ChecklistItem, type Readiness } from '../../api/endpoints/dfmFindings';
import type { CreationView } from '../../api/endpoints/create';
import type { TwinNode } from '../../types/twin';
import { BUILD_STATUS_COPY, ROUTE_COPY } from './copy';
import { InspectPanel } from './InspectPanel';
import { MakePanel } from './MakePanel';
import { ProductStage } from './ProductStage';
import { StageActions, type StageAction } from './StageActions';

/**
 * The words Create may show for making the product (section 16.2 item 7).
 * "Ready for prototype" and "Ready for production" appear only when the
 * release checklist backs them. Never a percentage.
 */
export function readinessText(readiness: Readiness): string {
  if (readiness.production) return 'Ready for production';
  if (readiness.prototype) return 'Ready for prototype';
  const n = readiness.next_steps.length;
  return n ? `Not ready to make yet: ${n} next step${n === 1 ? '' : 's'}` : 'Not ready to make yet';
}

function ItemIcon({ status }: { status: ChecklistItem['status'] }) {
  if (status === 'pass') return <CircleCheck size={13} aria-hidden="true" />;
  if (status === 'fail') return <CircleAlert size={13} aria-hidden="true" />;
  return <CircleDashed size={13} aria-hidden="true" />;
}

type Detail = 'plan' | 'inspect' | null;

interface MakeStageProps {
  projectId: string;
  view: CreationView | undefined;
  model: TwinNode | undefined;
  modelRevision: number | null;
  onAsk: (text: string) => void;
  /** The project's twin nodes, so Make can show the model a build makes. */
  nodes?: TwinNode[];
}

/**
 * Make: the model the newest live build makes, the release checklist and
 * what has been made, with one primary action. Planning a build (route,
 * process, release) and inspecting the result open in place, one at a time.
 */
export function MakeStage({ projectId, view, model, modelRevision, onAsk, nodes = [] }: MakeStageProps) {
  const checklist = useQuery({
    queryKey: dfmKeys.checklist(projectId),
    queryFn: ({ signal }) => getReleaseChecklist(projectId, signal),
  });
  const [detail, setDetail] = useState<Detail>(null);

  const builds = [...(view?.creation.builds ?? [])].reverse();
  const latest = builds[0];
  const toInspect = !!latest && latest.status === 'completed' && !latest.acceptance;
  // The revision a person released is the thing to look at here, not whatever
  // Develop last showed: the newest build that is not cancelled.
  const shown = builds.find((b) => b.status !== 'cancelled');
  const shownNode = shown ? nodes.find((n) => n.id === shown.revision.work_product_id) : undefined;

  const plan: StageAction = { id: 'plan', label: 'Plan the making', onClick: () => setDetail('plan') };
  const inspect: StageAction = { id: 'inspect', label: 'Inspect the result', onClick: () => setDetail('inspect') };
  const primary = toInspect ? inspect : plan;
  const more: StageAction[] = [
    ...(toInspect ? [plan] : builds.length ? [inspect] : []),
    { id: 'ask', label: 'Ask the assistant to prepare the release', onClick: () => onAsk('Prepare the release for making: ') },
  ];

  const data = checklist.data;
  const items = data?.items ?? [];

  return (
    <div className="cv-stage-col">
      <StageActions primary={primary} more={more} />

      {shown && (
        <section className="cv-card cv-make-model" aria-labelledby="cv-make-model-title" data-testid="make-model">
          <h3 id="cv-make-model-title">
            Build {shown.number} · {shown.revision.label || shown.revision.work_product_id}
          </h3>
          {shownNode ? (
            <ProductStage projectId={projectId} nodes={nodes} selectedId={shownNode.id} working={false} />
          ) : (
            <p className="cv-muted">This build's model is not in the twin, so it cannot be shown.</p>
          )}
        </section>
      )}

      {detail && view && (
        <section className="cv-card" aria-label={detail === 'plan' ? 'Plan the making' : 'Inspect the result'}>
          <div className="cv-card-head">
            <h3>{detail === 'plan' ? 'Plan the making' : 'Inspect the result'}</h3>
            <button type="button" className="cv-link" onClick={() => setDetail(null)}>
              Close
            </button>
          </div>
          {detail === 'plan' ? (
            <MakePanel projectId={projectId} view={view} model={model} modelRevision={modelRevision} />
          ) : (
            <InspectPanel projectId={projectId} view={view} />
          )}
        </section>
      )}

      <section className="cv-card" aria-labelledby="cv-release-title" data-testid="release-checklist">
        <h3 id="cv-release-title">Release checklist</h3>
        {data && data.applies ? (
          <>
            <p className="cv-muted">{readinessText(data.readiness)}</p>
            {items.length > 0 && (
              <ul className="cv-blockers cv-checklist">
                {items.map((item) => (
                  // The icon keeps its own column and the text wraps beside it.
                  // A wrapping row moved a long item's text to the next line and
                  // left its icon alone on a row with no label.
                  <li key={item.id} data-testid={`release-item-${item.id}`}>
                    <ItemIcon status={item.status} />
                    <span className="cv-checklist-text">
                      {item.label || item.id}
                      {item.detail && <span className="cv-muted"> · {item.detail}</span>}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <p className="cv-muted">
            {checklist.isLoading ? 'Loading…' : 'Applies once a making process with exact manufacturing checks is chosen.'}
          </p>
        )}
      </section>

      {detail !== 'plan' && (
        <section className="cv-card" aria-labelledby="cv-outputs-title" data-testid="make-outputs">
          <h3 id="cv-outputs-title">Builds</h3>
          {builds.length ? (
            <ul className="cv-outputs">
              {builds.map((b) => (
                <li key={b.id}>
                  <strong>Build {b.number}</strong>
                  <span className="cv-tag">{BUILD_STATUS_COPY[b.status]}</span>
                  <span className="cv-muted">
                    {b.revision.label || b.revision.work_product_id} · {ROUTE_COPY[b.route].label}
                    {b.process ? ` · ${b.process}` : ''}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="cv-muted">No builds yet.</p>
          )}
        </section>
      )}
    </div>
  );
}
