import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  Maximize2,
  MessageSquare,
  Minimize2,
  PanelLeftClose,
  PanelRightClose,
  Wrench,
} from 'lucide-react';
import { useProject } from '../hooks/use-projects';
import { useCreation } from '../hooks/use-creation';
import { useTwinNodes, useNodeVersionHistory } from '../hooks/use-twin';
import { useHarnessTools } from '../hooks/use-harness';
import { useApprovals } from '../hooks/use-approvals';
import { useProjectStore } from '../store/project-store';
import { useCreateStore, type Intent } from '../store/create-store';
import { useCreateLayoutStore } from '../store/create-layout-store';
import { refusalOf } from '../api/endpoints/create';
import { CreationsHome } from '../components/create/CreationsHome';
import { CreateConversation } from '../components/create/CreateConversation';
import { CreateComposer } from '../components/create/CreateComposer';
import { ProductStage, defaultStageNode, isEngineering, isVisual } from '../components/create/ProductStage';
import { JourneyRail } from '../components/create/JourneyRail';
import { ReadinessCard } from '../components/create/ReadinessCard';
import { BriefPanel } from '../components/create/BriefPanel';
import { DecisionsPanel } from '../components/create/DecisionsPanel';
import { MakePanel } from '../components/create/MakePanel';
import { InspectPanel } from '../components/create/InspectPanel';
import { JOURNEY, STEP_MILESTONE, milestoneIndex, milestoneSummaries } from '../components/create/copy';
import { ActivityOverlay } from '../components/activity/ActivityOverlay';
import { useOperations } from '../hooks/use-operations';
import { useOperationActions } from '../hooks/use-operation-actions';
import { exactChecksState } from '../lib/exact-checks';
import '../styles/create.css';
import '../styles/create-stage.css';

const PANELS = ['brief', 'decisions', 'make', 'inspect', 'history'] as const;
type Panel = (typeof PANELS)[number];
const PANEL_LABEL: Record<Panel, string> = {
  brief: 'Brief',
  decisions: 'Decisions',
  make: 'Make',
  inspect: 'Inspect',
  history: 'History',
};

/** Which side panel tells each journey step's story in presentation mode. */
const STEP_PANEL: Panel[] = ['brief', 'brief', 'brief', 'decisions', 'decisions', 'make', 'inspect', 'inspect'];
const STEP_CAPTION = [
  'Describe it in your own words. Photos and sketches help.',
  'MetaForge turns the request into a brief and asks only what matters now.',
  'Designs appear on stage as they exist. A concept is labelled as a concept.',
  'Checks run on the dimensioned model. Readiness says what is still in the way.',
  'Each commitment is its own decision: the design, sharing, spending, the machine.',
  'Choose home, workshop or a manufacturer, and release one exact revision.',
  'Inspect what came out against the plan, with photos and measurements.',
  'Accept it, or describe the correction and make it again in the same creation.',
];

/** Route-mapped intents to the tools each may use. */
function useIntentTools(): Record<Intent, string[]> {
  const tools = useHarnessTools();
  return useMemo(() => {
    const ids = (tools.data ?? []).map((t) => t.id);
    return {
      ask: [],
      change: ids.includes('twin.propose_change') ? ['twin.propose_change'] : [],
      design: ids,
    };
  }, [tools.data]);
}

export function CreatePage() {
  const { projectId } = useParams<{ projectId: string }>();
  return projectId ? <CreateWorkspace projectId={projectId} /> : <CreationsHome />;
}

function CreateWorkspace({ projectId }: { projectId: string }) {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const panel = (PANELS as readonly string[]).includes(params.get('panel') ?? '') ? (params.get('panel') as Panel) : 'brief';
  const presenting = params.get('present') === '1';
  const setActiveProject = useProjectStore((s) => s.setActiveProject);

  const project = useProject(projectId);
  const view = useCreation(projectId);
  const nodes = useTwinNodes(projectId);
  const pending = useApprovals({ status: 'pending', projectId });
  const tools = useIntentTools();
  const session = useCreateStore((s) => s.session(projectId));
  const selectNode = useCreateStore((s) => s.selectNode);
  const setDraft = useCreateStore((s) => s.setDraft);
  const setIntent = useCreateStore((s) => s.setIntent);
  const submit = useCreateStore((s) => s.submit);
  // Operations come from the gateway (FORGE-786) and are shared with
  // Engineering. While the project has any, `working` is derived from them,
  // plus an instruction still on its way out (acknowledged before its
  // operation exists). Without any, the local queue is the fallback.
  const operations = useOperations(projectId);
  // FORGE-788: the overlay's cancel and retry call the gateway; a review
  // opens the Decisions panel, Create's own approval flow.
  const operationAction = useOperationActions(projectId);
  const queueWorking = session.queue.some((i) => i.state === 'sending' || i.state === 'queued');
  const working = operations.known
    ? operations.active.length > 0 || session.queue.some((i) => i.state === 'sending')
    : queueWorking;
  // CDE slice 6: exact geometry checks behind the preview, said in plain words.
  const engineeringChecks = exactChecksState(operations.operations.byId);

  // Opening a creation makes it the workspace's project, so Engineering shows the same one.
  useEffect(() => {
    setActiveProject(projectId);
  }, [projectId, setActiveProject]);

  // A creation started from the home screen sends its first request once.
  const started = useRef(false);
  useEffect(() => {
    if (params.get('start') === '1' && !started.current && tools.ask) {
      started.current = true;
      if (useCreateStore.getState().session(projectId).draft.trim()) submit(projectId, tools);
      const next = new URLSearchParams(params);
      next.delete('start');
      setParams(next, { replace: true });
    }
  }, [params, projectId, setParams, submit, tools]);

  const projectNodes = useMemo(() => nodes.data ?? [], [nodes.data]);
  const visual = projectNodes.filter(isVisual);
  const stageNode = visual.find((n) => n.id === session.selectedNodeId) ?? defaultStageNode(visual);
  const model = stageNode && isEngineering(stageNode) ? stageNode : defaultStageNode(visual.filter(isEngineering));
  const history = useNodeVersionHistory(model?.id);
  const modelRevision = history.data?.length ? Math.max(...history.data.map((r) => r.revision)) : null;

  const openPanel = useCallback(
    (p: Panel, replace = false) => {
      const next = new URLSearchParams(params);
      next.set('panel', p);
      setParams(next, { replace });
    },
    [params, setParams],
  );

  const milestone = milestoneIndex(view.data);
  const summaries = useMemo(() => milestoneSummaries(view.data), [view.data]);
  const collapsed = useCreateLayoutStore((s) => s.collapsed);
  const setCollapsed = useCreateLayoutStore((s) => s.setCollapsed);
  const chatCollapsed = collapsed.chat && !presenting;
  const sideCollapsed = collapsed.side && !presenting;

  /** Describe: open the conversation and put the cursor in the composer. */
  const focusComposer = useCallback(() => {
    setCollapsed('chat', false);
    window.setTimeout(() => {
      document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Describe your product or ask for a change"]')?.focus();
    }, 0);
  }, [setCollapsed]);

  const showPanel = useCallback(
    (p: Panel) => {
      setCollapsed('side', false);
      openPanel(p);
    },
    [openPanel, setCollapsed],
  );

  const [tourStep, setTourStep] = useState(0);
  const root = useRef<HTMLDivElement>(null);

  const setPresenting = (on: boolean) => {
    const next = new URLSearchParams(params);
    if (on) next.set('present', '1');
    else next.delete('present');
    setParams(next);
    if (on) {
      // A presentation tells the whole loop, from the first step.
      setTourStep(0);
      void root.current?.requestFullscreen?.().catch(() => undefined);
    } else if (document.fullscreenElement) {
      void document.exitFullscreen().catch(() => undefined);
    }
  };

  useEffect(() => {
    if (!presenting) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') setTourStep((s) => Math.min(JOURNEY.length - 1, s + 1));
      if (e.key === 'ArrowLeft') setTourStep((s) => Math.max(0, s - 1));
      if (e.key === 'Escape') {
        const next = new URLSearchParams(params);
        next.delete('present');
        setParams(next);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [presenting, params, setParams]);

  useEffect(() => {
    if (presenting) openPanel(STEP_PANEL[tourStep] ?? 'brief', true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presenting, tourStep]);

  if (project.isError) {
    return (
      <div className="workspace-empty">
        <h3>This creation could not be opened</h3>
        <p>{refusalOf(project.error)}</p>
        <Link className="text-action" to="/create">
          Back to your creations
        </Link>
      </div>
    );
  }

  const pendingCount = pending.data?.items.length ?? 0;
  const blockerPanel = (id: string): Panel =>
    id === 'approvals' || id.startsWith('wp:') || id === 'design:model'
      ? 'decisions'
      : id.startsWith('check:') || id === 'inspection_plan'
        ? 'inspect'
        : id.startsWith('measurement:')
          ? 'brief'
          : 'make';

  return (
    <div ref={root} className={`cv-root ${presenting ? 'is-presenting' : ''}`}>
      <header className="cv-head">
        <div className="cv-title">
          <Link to="/create" className="cv-back" aria-label="All creations">
            <ChevronLeft size={16} />
          </Link>
          <h1>{project.data?.name ?? 'Loading…'}</h1>
        </div>
        <JourneyRail milestone={presenting ? (STEP_MILESTONE[tourStep] ?? 0) : milestone} summaries={summaries} active={working} />
        <div className="cv-head-actions">
          <button
            type="button"
            className="cv-btn"
            onClick={() => setPresenting(!presenting)}
            aria-pressed={presenting}
            title={presenting ? 'Leave presentation (Esc)' : 'Present this creation'}
          >
            {presenting ? <Minimize2 size={14} /> : <Maximize2 size={14} />} {presenting ? 'Exit' : 'Present'}
          </button>
          <button type="button" className="cv-btn" onClick={() => navigate(`/projects/${projectId}`)} title="The same creation, with every engineering detail">
            <Wrench size={14} /> Engineering
          </button>
        </div>
      </header>

      {presenting && (
        <div className="cv-tour" role="status">
          <button type="button" className="cv-icon-btn" aria-label="Previous step" onClick={() => setTourStep((s) => Math.max(0, s - 1))} disabled={tourStep === 0}>
            <ChevronLeft size={16} />
          </button>
          <p>
            <strong>{JOURNEY[tourStep]}.</strong> {STEP_CAPTION[tourStep]}
          </p>
          <button
            type="button"
            className="cv-icon-btn"
            aria-label="Next step"
            onClick={() => setTourStep((s) => Math.min(JOURNEY.length - 1, s + 1))}
            disabled={tourStep === JOURNEY.length - 1}
          >
            <ChevronRight size={16} />
          </button>
        </div>
      )}

      <div className={`cv-body${chatCollapsed ? ' is-chat-collapsed' : ''}${sideCollapsed ? ' is-side-collapsed' : ''}`}>
        {/*
          The activity card has its own row above the stage, so the stage's
          toolbars and panels (Generate, the editable sketch) are never under
          it (ASVG-005). It comes first in the page so a keyboard reaches it
          straight after the header (ASVG-012); the grid puts it over the stage.
        */}
        {!presenting && (
          <div className="cv-activity-slot" data-testid="create-activity-slot">
            <ActivityOverlay
              projectId={projectId}
              className="act-dock-create"
              onAction={operationAction}
              onApproval={() => showPanel('decisions')}
            />
          </div>
        )}
        {chatCollapsed ? (
          <aside className="cv-rail cv-rail-chat" aria-label="Conversation" data-testid="chat-rail">
            <button type="button" className="cv-icon-btn" aria-label="Show the conversation" title="Show the conversation" onClick={() => setCollapsed('chat', false)}>
              <MessageSquare size={16} />
            </button>
            {session.queue.length > 0 && (
              <span className="cv-count" title="Instructions waiting">
                {session.queue.length}
              </span>
            )}
          </aside>
        ) : (
          <aside className="cv-chat" aria-label="Conversation">
            {!presenting && (
              <button
                type="button"
                className="cv-icon-btn cv-collapse"
                aria-label="Collapse the conversation"
                title="Collapse the conversation"
                onClick={() => setCollapsed('chat', true)}
              >
                <PanelLeftClose size={14} />
              </button>
            )}
            <CreateConversation projectId={projectId} tools={tools} />
            <CreateComposer projectId={projectId} tools={tools} busy={working} />
          </aside>
        )}

        <main className="cv-main">
          <ProductStage
            projectId={projectId}
            nodes={projectNodes}
            selectedId={session.selectedNodeId}
            onSelect={(id) => selectNode(projectId, id)}
            onDiscuss={(n) => {
              selectNode(projectId, n.id);
              const draft = useCreateStore.getState().session(projectId).draft;
              setDraft(projectId, `About ${n.name}: ${draft}`);
            }}
            working={working}
            presenting={presenting}
            engineeringChecks={engineeringChecks}
            facts={{
              described: !!view.data && Object.values(view.data.creation.brief.items).some((i) => i && i.status !== 'missing'),
              hasReferences: (view.data?.creation.brief.references.length ?? 0) > 0,
            }}
            onDescribe={focusComposer}
            onBuildExact={() => {
              setIntent(projectId, 'design');
              setDraft(projectId, 'Build an exact, dimensioned model from the accepted concept.');
              focusComposer();
            }}
            onPrepare={() => showPanel('make')}
          />
        </main>

        {sideCollapsed ? (
          <aside className="cv-rail cv-rail-side" aria-label="Details" data-testid="side-rail">
            <button type="button" className="cv-icon-btn" aria-label="Show the details" title="Show the details" onClick={() => setCollapsed('side', false)}>
              <ClipboardList size={16} />
            </button>
            {PANELS.map((p) => (
              <button
                key={p}
                type="button"
                className="cv-rail-tab"
                title={PANEL_LABEL[p]}
                aria-label={p === 'decisions' && pendingCount > 0 ? `${PANEL_LABEL[p]}, ${pendingCount} waiting` : PANEL_LABEL[p]}
                onClick={() => showPanel(p)}
              >
                {PANEL_LABEL[p].slice(0, 1)}
                {p === 'decisions' && pendingCount > 0 && (
                  <span className="cv-count" data-testid="decisions-badge">
                    {pendingCount}
                  </span>
                )}
              </button>
            ))}
          </aside>
        ) : (
        <aside className="cv-side" aria-label="Details">
          {!presenting && (
            <button
              type="button"
              className="cv-icon-btn cv-collapse"
              aria-label="Collapse the details"
              title="Collapse the details"
              onClick={() => setCollapsed('side', true)}
            >
              <PanelRightClose size={14} />
            </button>
          )}
          {view.data ? (
            <ReadinessCard readiness={view.data.readiness} onAction={(id) => showPanel(blockerPanel(id))} />
          ) : view.isError ? (
            <p className="cv-error">Readiness could not be loaded: {refusalOf(view.error)}</p>
          ) : (
            <p className="cv-muted">Loading…</p>
          )}
          <div className="cv-tabs" role="tablist" aria-label="Creation details">
            {PANELS.map((p) => (
              <button
                key={p}
                type="button"
                role="tab"
                id={`cv-tab-${p}`}
                aria-selected={panel === p}
                aria-controls="cv-tabpanel"
                className={`cv-tab ${panel === p ? 'is-on' : ''}`}
                onClick={() => openPanel(p)}
              >
                {PANEL_LABEL[p]}
                {p === 'decisions' && pendingCount > 0 && (
                  <span className="cv-count" data-testid="decisions-badge" title={`${pendingCount} waiting for you`}>
                    {pendingCount}
                  </span>
                )}
              </button>
            ))}
          </div>
          <div id="cv-tabpanel" role="tabpanel" aria-labelledby={`cv-tab-${panel}`} className="cv-tabpanel">
            {view.data &&
              (panel === 'brief' ? (
                <BriefPanel projectId={projectId} view={view.data} />
              ) : panel === 'decisions' ? (
                <DecisionsPanel projectId={projectId} />
              ) : panel === 'make' ? (
                <MakePanel projectId={projectId} view={view.data} model={model} modelRevision={modelRevision} />
              ) : panel === 'inspect' ? (
                <InspectPanel projectId={projectId} view={view.data} />
              ) : (
                <ol className="cv-history">
                  {[...view.data.creation.history].reverse().map((h, i) => (
                    <li key={`${h.at}-${i}`}>
                      <time dateTime={h.at}>{new Date(h.at).toLocaleString()}</time>
                      <span>{h.summary || h.change}</span>
                      <span className="cv-muted">
                        {h.actor} · {h.surface}
                      </span>
                    </li>
                  ))}
                  {view.data.creation.history.length === 0 && <li className="cv-muted">Nothing recorded yet.</li>}
                </ol>
              ))}
          </div>
        </aside>
        )}
      </div>
    </div>
  );
}
