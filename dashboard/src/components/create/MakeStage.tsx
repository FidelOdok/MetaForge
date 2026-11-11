import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { CircleAlert, CircleCheck, CircleDashed, ListChecks, Package } from 'lucide-react';
import { dfmKeys, getReleaseChecklist, type ChecklistItem, type Readiness } from '../../api/endpoints/dfmFindings';
import type { CreationView } from '../../api/endpoints/create';
import type { TwinNode } from '../../types/twin';
import { BUILD_STATUS_COPY, ROUTE_COPY } from './copy';
import { InspectPanel } from './InspectPanel';
import { MakePanel } from './MakePanel';
import { ProductStage } from './ProductStage';
import { StageActions, type StageAction } from './StageActions';
import { StageDisclosures, useRevealOnOpen } from './StageDisclosures';

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

/** What is open under the model: planning, inspecting, or one of the detail buttons. */
type Detail = 'plan' | 'inspect' | 'checklist' | 'builds' | null;

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
 * Make: the model the newest live build makes, large and first. Under it,
 * one primary action and buttons for the release checklist and the builds;
 * each opens in place, one at a time, as do planning and inspecting.
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
  const counted = items.filter((i) => i.status !== 'not_applicable');
  const passed = counted.filter((i) => i.status === 'pass').length;
  const checklistBadge = !data
    ? checklist.isLoading
      ? '…'
      : undefined
    : !data.applies
      ? 'not yet'
      : data.readiness.production || data.readiness.prototype
        ? readinessText(data.readiness)
        : `${passed} of ${counted.length} pass`;
  const checklistTone = data?.applies ? (counted.length && passed === counted.length ? 'ok' : 'warn') : undefined;
  const actionOpen = detail === 'plan' || detail === 'inspect';
  const actionPanel = useRevealOnOpen<HTMLElement>(actionOpen ? detail : null);

  return (
    <div className="cv-stage-col is-model">
      {shown && (
        <section className="cv-make-model" aria-labelledby="cv-make-model-title" data-testid="make-model">
          <h3 id="cv-make-model-title" className="cv-make-model-title">
            Build {shown.number} · {shown.revision.label || shown.revision.work_product_id}
          </h3>
          {shownNode ? (
            <ProductStage projectId={projectId} nodes={nodes} selectedId={shownNode.id} working={false} />
          ) : (
            <p className="cv-card cv-muted">This build's model is not in the twin, so it cannot be shown.</p>
          )}
        </section>
      )}

      <StageDisclosures
        open={actionOpen ? null : detail}
        onOpen={(id) => setDetail(id as Detail)}
        lead={<StageActions primary={primary} more={more} />}
        items={[
          {
            id: 'checklist',
            label: 'Release checklist',
            badge: checklistBadge,
            tone: checklistTone,
            icon: <ListChecks size={14} aria-hidden="true" />,
            content: (
              <div data-testid="release-checklist">
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
              </div>
            ),
          },
          {
            id: 'builds',
            label: 'Builds',
            badge: String(builds.length),
            icon: <Package size={14} aria-hidden="true" />,
            content: (
              <div data-testid="make-outputs">
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
              </div>
            ),
          },
        ]}
      />

      {actionOpen && view && (
        <section ref={actionPanel} className="cv-card" aria-label={detail === 'plan' ? 'Plan the making' : 'Inspect the result'}>
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
    </div>
  );
}
