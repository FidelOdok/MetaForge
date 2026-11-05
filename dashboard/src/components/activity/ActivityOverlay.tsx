import { useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { ChevronDown, ChevronUp, Pause, Play, X } from 'lucide-react';

import { cancelOperation, retryOperation } from '../../api/endpoints/operations';
import { refusalOf } from '../../api/endpoints/create';
import { useImpact, useInterfaces } from '../../hooks/use-impact';
import { useTwinNode } from '../../hooks/use-twin';
import { sortedOperations, type OperationView } from '../../lib/operations-reducer';
import { useOperationsStore, useProjectOperations } from '../../store/operations-store';
import { impactIdOf } from '../../types/impact';
import { isTerminal, type ActionRef, type ArtifactRef, type OperationDomain } from '../../types/operations';
import { PreviewHost } from '../preview/PreviewHost';
import { ImpactPanel } from './ImpactPanel';
import { InlineApproval } from './InlineApproval';
import {
  authorityLabel,
  currentStep,
  domainLabel,
  elapsedText,
  type InspectTarget,
  nextAction,
  progressText,
  inspectTarget,
  realArtifact,
  statusLabel,
  statusTone,
} from './labels';
import { useNow, useOnScreen, usePrefersReducedMotion, useThrottledAnnouncement } from './motion';
import { sceneGraphFrom } from './scenes/graph';
import { sceneFor } from './scenes/registry';
import { sceneDomainOf, sceneStateOf } from './scenes/types';
import '../../styles/activity.css';

export interface ActivityOverlayProps {
  projectId: string;
  /**
   * Engineering density: lanes per domain with stage ids, revisions, severity
   * and the raw event messages on expand. Create leaves it off.
   */
  detail?: boolean;
  /**
   * Handles `cancel`, `retry` and `open` (FORGE-788). A returned promise that
   * rejects shows its refusal beside the actions. Without one, cancel and
   * retry call the gateway directly and open goes to the run.
   */
  onAction?: (op: OperationView, action: ActionRef) => void | Promise<unknown>;
  /**
   * Handles `approval` when the host has its own approval flow (Create's
   * Decisions panel). Without one, the existing approval card opens inline.
   */
  onApproval?: (op: OperationView, action: ActionRef) => void;
  className?: string;
}

const CONNECTION_LOST = 'Connection interrupted, reconnecting';

/**
 * The activity overlay (FORGE-786, section 11B and 11C of the FRS).
 *
 * A compact card that expands into a prominent, slightly translucent overlay
 * and minimises back. It shows one primary operation at a time; any other
 * live operations collapse into a queue counter. It reads the shared
 * operations store, so Create and Engineering show the same operation, stage,
 * revision and preview. Minimising never cancels anything.
 *
 * Honesty rules: status words come from backend state only; a percentage is
 * shown only for a measured total; there is never an ETA; the scene is
 * labelled "Illustrative activity" until a real artifact arrives; and while
 * the connection is down the scene stops moving and says so.
 */
export function ActivityOverlay({ projectId, detail = false, onAction, onApproval, className }: ActivityOverlayProps) {
  const project = useProjectOperations(projectId);
  const setSize = useOperationsStore((s) => s.setSize);
  const dismiss = useOperationsStore((s) => s.dismiss);
  const setMotionPaused = useOperationsStore((s) => s.setMotionPaused);

  const ordered = useMemo(() => sortedOperations(project.ops, project.dismissed), [project.ops, project.dismissed]);
  const primary = ordered[0];
  const queued = ordered.slice(1).filter((op) => !isTerminal(op.state)).length;
  const connected = project.health === 'live';
  const expanded = project.size === 'expanded';

  // Nothing to show: no card, and no connection notice for an empty project.
  if (!primary) return null;

  return (
    <section
      className={`act-overlay is-${expanded ? 'expanded' : 'compact'}${detail ? ' is-detail' : ''}${className ? ` ${className}` : ''}`}
      aria-label="Activity"
      data-testid="activity-overlay"
    >
      {!connected && project.health !== 'connecting' && (
        <p className="act-connection" role="status">
          {CONNECTION_LOST}
        </p>
      )}
      <PrimaryOperation
          key={primary.operation_id}
          projectId={projectId}
          op={primary}
          queued={queued}
          expanded={expanded}
          detail={detail}
          connected={connected}
          motionPaused={project.motionPaused}
          onToggleSize={() => setSize(projectId, expanded ? 'compact' : 'expanded')}
          onTogglePause={() => setMotionPaused(projectId, !project.motionPaused)}
          onDismiss={() => dismiss(projectId, primary.operation_id)}
          onAction={onAction}
          onApproval={onApproval}
        />
    </section>
  );
}

interface PrimaryProps {
  projectId: string;
  op: OperationView;
  queued: number;
  expanded: boolean;
  detail: boolean;
  connected: boolean;
  motionPaused: boolean;
  onToggleSize: () => void;
  onTogglePause: () => void;
  onDismiss: () => void;
  onAction?: (op: OperationView, action: ActionRef) => void | Promise<unknown>;
  onApproval?: (op: OperationView, action: ActionRef) => void;
}

function PrimaryOperation({
  projectId,
  op,
  queued,
  expanded,
  detail,
  connected,
  motionPaused,
  onToggleSize,
  onTogglePause,
  onDismiss,
  onAction,
  onApproval,
}: PrimaryProps) {
  const label = statusLabel(op);
  const tone = statusTone(op.state);
  const terminal = isTerminal(op.state);
  const now = useNow(!terminal && connected);
  // ASVG-016: no figure at all rather than "NaNs" when the start is unknown.
  const elapsed = elapsedText(op, now);
  const step = currentStep(op);
  const progress = progressText(op);
  const urgent = op.state === 'failed' || op.state === 'waiting_for_user' || op.state === 'blocked';
  // ASVG-015: the stage whose detail is open, '' for the operation itself.
  const [openStage, setOpenStage] = useState<string | null>(null);
  // FORGE-793: a cross-domain impact operation names its recorded impact.
  const impactId = impactIdOf(op.operation_id);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const announcement = useThrottledAnnouncement(
    connected ? `${op.title}: ${label}. ${step}` : `${op.title}: ${CONNECTION_LOST}`,
    urgent || !connected,
  );

  return (
    <div
      className={`act-card tone-${tone}`}
      data-state={op.state}
      data-operation={op.operation_id}
      // Escape minimises the expanded overlay and hands focus back to its toggle.
      onKeyDown={(e) => {
        if (e.key !== 'Escape' || !expanded) return;
        e.stopPropagation();
        onToggleSize();
        toggleRef.current?.focus();
      }}
    >
      <div className="act-head">
        <span className={`act-status tone-${tone}`} data-testid="activity-status">
          <span className="act-status-dot" aria-hidden="true" />
          {label}
        </span>
        <h2 className="act-title">{op.title}</h2>
        {elapsed !== null && (
          <span className="act-elapsed" title="Time elapsed" data-testid="activity-elapsed">
            {elapsed}
          </span>
        )}
        {queued > 0 && (
          <span className="act-queue" aria-label={`${queued} more ${queued === 1 ? 'operation' : 'operations'} in progress`}>
            +{queued}
          </span>
        )}
        <div className="act-head-actions">
          <button
            ref={toggleRef}
            type="button"
            className="act-icon"
            data-testid="activity-toggle"
            onClick={onToggleSize}
            aria-expanded={expanded}
            aria-label={expanded ? 'Minimise activity' : 'Expand activity'}
            title={expanded ? 'Minimise (keeps running, or press Escape)' : 'Expand'}
          >
            {expanded ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
          </button>
          {terminal && (
            <button type="button" className="act-icon" onClick={onDismiss} aria-label="Close this activity" title="Close">
              <X size={14} />
            </button>
          )}
        </div>
      </div>

      <p className="act-step">{step}</p>
      {progress && op.progress && (
        <div className="act-progress">
          <progress max={op.progress.total} value={Math.min(op.progress.done, op.progress.total)} aria-label="Measured progress" />
          <span>{progress}</span>
        </div>
      )}

      <div className="act-live" aria-live="polite" aria-atomic="true">
        {announcement}
      </div>

      {impactId && <ImpactPanel projectId={projectId} impactId={impactId} detail={detail} />}

      <Actions
        projectId={projectId}
        op={op}
        onAction={onAction}
        onApproval={onApproval}
        // Create shows one next action for an impact (UXS-015): the summary's.
        hideApprovals={!!impactId && !detail}
      />

      {expanded && (
        <div className="act-body">
          <Visual
            projectId={projectId}
            impactId={impactId}
            op={op}
            connected={connected}
            motionPaused={motionPaused}
            onTogglePause={onTogglePause}
            onInspectStage={() => setOpenStage(currentStageId(op))}
          />
          <p className="act-next">
            <span className="act-label">Next</span> {nextAction(op)}
          </p>
          <Milestones op={op} />
          <Lanes
            op={op}
            detail={detail}
            openStage={openStage}
            onToggleStage={(id) => setOpenStage((cur) => (cur === id ? null : id))}
          />
          {openStage !== null && <StageDetail op={op} stageId={openStage} onClose={() => setOpenStage(null)} />}
          {detail && <Detail op={op} />}
        </div>
      )}
    </div>
  );
}

/** The stage being worked on, else the latest one, else '' (the operation itself). */
function currentStageId(op: OperationView): string {
  const live = [...op.stages].reverse().find((s) => !isTerminal(s.state));
  return (live ?? op.stages[op.stages.length - 1])?.stage_id ?? '';
}

function Visual({
  projectId,
  impactId,
  op,
  connected,
  motionPaused,
  onTogglePause,
  onInspectStage,
}: {
  projectId: string;
  impactId: string | null;
  op: OperationView;
  connected: boolean;
  motionPaused: boolean;
  onTogglePause: () => void;
  onInspectStage: () => void;
}) {
  const navigate = useNavigate();
  const target = inspectTarget(op);
  const ref = useRef<HTMLDivElement>(null);
  const onScreen = useOnScreen(ref);
  const reducedMotion = usePrefersReducedMotion();
  // FORGE-793: the cross-domain scene draws the real dependency graph: the
  // project's interface edges, and the impact's own graph when it is one.
  const crossDomain = sceneDomainOf(op.domain) === 'cross_domain';
  const interfaces = useInterfaces(projectId, crossDomain);
  const impact = useImpact(projectId, impactId);
  const graph = crossDomain ? sceneGraphFrom(interfaces.data, impact.data) : null;
  // An impact's artifact refs point at what it affects, not at a preview of it.
  const artifact = impactId ? null : realArtifact(op);
  const Scene = sceneFor(op.domain);
  const stageIndex = Math.max(
    op.stages.filter((s) => s.state === 'completed').length,
    op.milestones.length,
    op.state === 'completed' ? 99 : 0,
  );
  // Motion that would imply progress stops while the connection is down,
  // while offscreen, and when the person pauses it.
  const paused = motionPaused || !onScreen || !connected;

  return (
    <div className="act-visual" ref={ref}>
      {artifact ? (
        <figure className="act-artifact">
          <figcaption className="act-visual-label">
            {`${authorityLabel(artifact)}: ${artifact.label}`}
            {target && <InspectLink target={target} />}
          </figcaption>
          <ArtifactPreview artifact={artifact} />
        </figure>
      ) : (
        <figure className="act-scene-wrap">
          <figcaption className="act-visual-label" data-testid="activity-visual-label">
            {graph ? 'Design dependencies' : 'Illustrative activity'}
          </figcaption>
          {/* ASVG-015: the drawing opens what it depicts, or the stage's detail. */}
          <button
            type="button"
            className="act-scene-btn"
            data-testid="activity-inspect"
            aria-label={target ? `Inspect ${target.label}` : 'Show the details of this step'}
            onClick={() => {
              if (!target) onInspectStage();
              else if (target.external) window.open(target.href, '_blank', 'noopener,noreferrer');
              else navigate(target.href);
            }}
          >
            <Scene
              state={sceneStateOf(op.state)}
              stageIndex={stageIndex}
              reducedMotion={reducedMotion}
              paused={paused}
              staleRevision={op.target_revision}
              graph={graph}
            />
          </button>
          {!reducedMotion && op.state === 'running' && (
            <button
              type="button"
              className="act-icon act-pause"
              onClick={onTogglePause}
              aria-pressed={motionPaused}
              aria-label={motionPaused ? 'Play animation' : 'Pause animation'}
            >
              {motionPaused ? <Play size={12} /> : <Pause size={12} />}
            </button>
          )}
        </figure>
      )}
    </div>
  );
}

/** A real artifact: a twin work product through PreviewHost, an image, or a link. */
function ArtifactPreview({ artifact }: { artifact: ArtifactRef }) {
  if (artifact.kind === 'work_product') return <WorkProductPreview artifact={artifact} />;
  if (artifact.kind === 'image' && artifact.url) {
    return <img className="act-image" src={artifact.url} alt={artifact.label} />;
  }
  return artifact.url ? (
    <a className="act-link" href={artifact.url} target="_blank" rel="noreferrer">
      Open {artifact.label}
    </a>
  ) : (
    <p className="act-muted">{artifact.label}</p>
  );
}

function WorkProductPreview({ artifact }: { artifact: ArtifactRef }) {
  const node = useTwinNode(artifact.id);
  if (!node.data) return <p className="act-muted">{node.isError ? 'The preview could not be loaded.' : 'Loading the preview…'}</p>;
  return <PreviewHost node={node.data} mode="compact" />;
}

function Milestones({ op }: { op: OperationView }) {
  if (op.milestones.length === 0) return null;
  return (
    <div className="act-section">
      <h3 className="act-label">Done so far</h3>
      <ol className="act-milestones">
        {op.milestones.map((m) => (
          <li key={m.sequence}>{m.label}</li>
        ))}
      </ol>
    </div>
  );
}

/** A link to an artifact the operation produced (ASVG-015). */
function InspectLink({ target }: { target: InspectTarget }) {
  return target.external ? (
    <a className="act-inspect-link" href={target.href} target="_blank" rel="noreferrer">
      Open
    </a>
  ) : (
    <Link className="act-inspect-link" to={target.href}>
      Open
    </Link>
  );
}

/** One stage's state, its artifacts and the next action (ASVG-015). */
function StageDetail({ op, stageId, onClose }: { op: OperationView; stageId: string; onClose: () => void }) {
  const stage = op.stages.find((s) => s.stage_id === stageId);
  const title = stage?.stage_title || stage?.stage_id || op.title;
  const state = stage?.state ?? op.state;
  return (
    <section className="act-section act-stage-detail" aria-label={`Details: ${title}`} data-testid="activity-stage-detail">
      <div className="act-stage-detail-head">
        <h3 className="act-label">{title}</h3>
        <button type="button" className="act-icon" onClick={onClose} aria-label="Close the details">
          <X size={12} />
        </button>
      </div>
      <p>
        <span className="act-muted">Status</span> {statusLabel({ ...op, state })}
      </p>
      {op.artifact_refs.length > 0 ? (
        <ul className="act-stage-artifacts">
          {op.artifact_refs.map((a) => {
            const t = inspectTarget({ ...op, artifact_refs: [a] });
            return (
              <li key={`${a.kind}:${a.id}`}>
                {a.label || a.id}
                {t ? <InspectLink target={t} /> : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="act-muted">Nothing produced yet.</p>
      )}
      <p>
        <span className="act-muted">Next</span> {nextAction({ ...op, state })}
      </p>
    </section>
  );
}

/** Parallel work, one lane per domain (ASVG-007, RT-025). Each stage opens its detail. */
function Lanes({
  op,
  detail,
  openStage,
  onToggleStage,
}: {
  op: OperationView;
  detail: boolean;
  openStage: string | null;
  onToggleStage: (stageId: string) => void;
}) {
  const lanes = useMemo(() => {
    const by = new Map<OperationDomain, OperationView['stages']>();
    for (const stage of op.stages) by.set(stage.domain, [...(by.get(stage.domain) ?? []), stage]);
    return [...by.entries()];
  }, [op.stages]);
  if (lanes.length === 0 || (!detail && lanes.length < 2)) return null;
  return (
    <div className="act-section">
      <h3 className="act-label">{detail ? 'Lanes' : 'Working on'}</h3>
      <ul className="act-lanes">
        {lanes.map(([domain, stages]) => {
          const latest = stages[stages.length - 1];
          return (
            <li key={domain} className={`act-lane tone-${statusTone(latest?.state ?? 'queued')}`} data-domain={domain}>
              <span className="act-lane-name">{domainLabel(domain, detail)}</span>
              {detail ? (
                <ul className="act-stages">
                  {stages.map((s) => (
                    <li key={s.stage_id}>
                      <button
                        type="button"
                        className="act-stage-btn"
                        aria-expanded={openStage === s.stage_id}
                        onClick={() => onToggleStage(s.stage_id)}
                      >
                        <code>{s.stage_id}</code> {s.stage_title ?? ''} <span className="act-muted">{s.state}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : latest ? (
                <button
                  type="button"
                  className="act-stage-btn act-muted"
                  aria-expanded={openStage === latest.stage_id}
                  onClick={() => onToggleStage(latest.stage_id)}
                >
                  {latest.stage_title ?? latest.stage_id}
                </button>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Engineering only: revisions, severity and the raw event lines, hidden until asked for. */
function Detail({ op }: { op: OperationView }) {
  return (
    <details className="act-detail">
      <summary>Technical detail</summary>
      <dl>
        <dt>Operation</dt>
        <dd>
          <code>{op.operation_id}</code>
        </dd>
        {op.run_id && (
          <>
            <dt>Run</dt>
            <dd>
              <Link to={`/runs/${encodeURIComponent(op.run_id)}`}>{op.run_id}</Link>
            </dd>
          </>
        )}
        <dt>Revision</dt>
        <dd>
          {op.base_revision ?? 'none'} to {op.target_revision ?? 'none'}
        </dd>
        <dt>Severity</dt>
        <dd>{op.severity}</dd>
      </dl>
      <ol className="act-log">
        {op.log.map((e) => (
          <li key={e.sequence} className={`sev-${e.severity}`}>
            <span className="act-muted">#{e.sequence}</span> <code>{e.type}</code>
            {e.stageId ? <code> {e.stageId}</code> : null}
            {e.targetRevision ? <span className="act-muted"> rev {e.targetRevision}</span> : null} {e.message}
          </li>
        ))}
      </ol>
    </details>
  );
}

/** Inline actions, exactly the ones the operation lists in `action_refs` (RT-028). */
function Actions({
  projectId,
  op,
  onAction,
  onApproval,
  hideApprovals = false,
}: {
  projectId: string;
  op: OperationView;
  onAction?: (op: OperationView, action: ActionRef) => void | Promise<unknown>;
  onApproval?: (op: OperationView, action: ActionRef) => void;
  /** The approvals are offered elsewhere in the card (an impact's summary). */
  hideApprovals?: boolean;
}) {
  const [openApproval, setOpenApproval] = useState<string | null>(null);
  // Cancel and retry go to the gateway: through the host when it handles
  // them, else directly. The gateway's refusal (a 409 says why) is shown.
  const run = useMutation({
    mutationFn: async (action: ActionRef) => {
      if (onAction) return onAction(op, action);
      if (action.kind === 'cancel') return cancelOperation(projectId, op.operation_id);
      if (action.kind === 'retry') return retryOperation(projectId, op.operation_id);
      return undefined;
    },
  });
  const actions = hideApprovals ? op.action_refs.filter((a) => a.kind !== 'approval') : op.action_refs;
  if (actions.length === 0) return null;
  const runHref = op.run_id ? `/runs/${encodeURIComponent(op.run_id)}` : null;

  return (
    <div className="act-actions-wrap">
      <div className="act-actions">
        {actions.map((action) => {
          const key = `${action.kind}:${action.id}`;
          if (action.kind === 'approval') {
            if (onApproval) {
              return (
                <button key={key} type="button" className="act-btn is-primary" onClick={() => onApproval(op, action)}>
                  {action.label || 'Review'}
                </button>
              );
            }
            return (
              <button
                key={key}
                type="button"
                className="act-btn is-primary"
                aria-expanded={openApproval === action.id}
                onClick={() => setOpenApproval((cur) => (cur === action.id ? null : action.id))}
              >
                {action.label || 'Review'}
              </button>
            );
          }
          if (action.kind === 'cancel' || action.kind === 'retry' || onAction) {
            return (
              <button key={key} type="button" className="act-btn" disabled={run.isPending} onClick={() => run.mutate(action)}>
                {action.label || (action.kind === 'cancel' ? 'Cancel' : action.kind === 'retry' ? 'Retry' : 'Open')}
              </button>
            );
          }
          const href = action.id.startsWith('/') ? action.id : runHref;
          return href ? (
            <Link key={key} className="act-btn" to={href}>
              {action.label}
            </Link>
          ) : null;
        })}
      </div>
      {run.isError && <p className="act-error">{refusalOf(run.error)}</p>}
      {openApproval && <InlineApproval id={openApproval} />}
    </div>
  );
}
