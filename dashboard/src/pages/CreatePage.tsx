import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { ChevronLeft, ClipboardCheck, MessageSquare, PanelLeftClose, X } from 'lucide-react';
import { useProject } from '../hooks/use-projects';
import { useCreation } from '../hooks/use-creation';
import { useAppearanceSync } from '../hooks/use-appearance';
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
import { ChatResizer } from '../components/create/ChatResizer';
import { defaultStageNode, isEngineering, isVisual } from '../components/create/ProductStage';
import { JourneyRail } from '../components/create/JourneyRail';
import { DecisionsPanel } from '../components/create/DecisionsPanel';
import { DevelopStage } from '../components/create/DevelopStage';
import { CheckStage } from '../components/create/CheckStage';
import { MakeStage } from '../components/create/MakeStage';
import { ImagineStage, type ImagineView } from '../components/create/imagine/ImagineStage';
import { milestoneIndex, milestoneSummaries } from '../components/create/copy';
import { ActivityOverlay } from '../components/activity/ActivityOverlay';
import { useOperations } from '../hooks/use-operations';
import { useOperationActions } from '../hooks/use-operation-actions';
import { exactChecksState } from '../lib/exact-checks';
import '../styles/create.css';
import '../styles/create-stage.css';

/** The four stages, as they appear in the address (`?stage=`). */
export const STAGE_IDS = ['imagine', 'develop', 'check', 'make'] as const;
export type StageId = (typeof STAGE_IDS)[number];

/**
 * Route-mapped intents to the tools each may use. A design turn sends no ids:
 * it carries the Create stage, and the gateway gives it that stage's tool set
 * (FORGE-825), so it never needs to cap or drop tools.
 */
function useIntentTools(): Record<Intent, string[]> {
  const tools = useHarnessTools();
  return useMemo(() => {
    const ids = (tools.data ?? []).map((t) => t.id);
    return {
      ask: [],
      change: ids.includes('twin.propose_change') ? ['twin.propose_change'] : [],
      design: [],
    };
  }, [tools.data]);
}

/** What the stage buttons ask the assistant to do (FORGE-824). */
export const START_IMAGINE_TEXT = 'Start the Imagine studio for this product.';
export const START_DEVELOP_TEXT = 'Imagine is handed over. Start developing this product.';

export function CreatePage() {
  const { projectId } = useParams<{ projectId: string }>();
  return projectId ? <CreateWorkspace projectId={projectId} /> : <CreationsHome />;
}

const COMPOSER = 'textarea[aria-label="Describe your product or ask for a change"]';

function CreateWorkspace({ projectId }: { projectId: string }) {
  const [params, setParams] = useSearchParams();
  const setActiveProject = useProjectStore((s) => s.setActiveProject);

  const project = useProject(projectId);
  const view = useCreation(projectId);
  // The appearance record, applied to every mesh the stage shows.
  useAppearanceSync(projectId);
  const nodes = useTwinNodes(projectId);
  const pending = useApprovals({ status: 'pending', projectId });
  const tools = useIntentTools();
  const session = useCreateStore((s) => s.session(projectId));
  const selectNode = useCreateStore((s) => s.selectNode);
  const setDraft = useCreateStore((s) => s.setDraft);
  const setIntent = useCreateStore((s) => s.setIntent);
  const submit = useCreateStore((s) => s.submit);
  const sendNow = useCreateStore((s) => s.sendNow);
  const setStage = useCreateStore((s) => s.setStage);
  const openDrawer = useCreateLayoutStore((s) => s.openDrawer);
  // Operations come from the gateway (FORGE-786) and are shared with
  // Engineering. While the project has any, `working` is derived from them,
  // plus an instruction still on its way out. Without any, the local queue
  // is the fallback.
  const operations = useOperations(projectId);
  const operationAction = useOperationActions(projectId);
  const queueWorking = session.queue.some((i) => i.state === 'sending' || i.state === 'queued');
  const working = operations.known
    ? operations.active.length > 0 || session.queue.some((i) => i.state === 'sending')
    : queueWorking;
  // CDE slice 6: exact geometry checks behind the preview, said in plain words.
  const engineeringChecks = exactChecksState(operations.operations.byId);
  const [decisionsOpen, setDecisionsOpen] = useState(false);

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

  const milestone = milestoneIndex(view.data);
  const summaries = useMemo(() => milestoneSummaries(view.data), [view.data]);
  const requested = STAGE_IDS.indexOf(params.get('stage') as StageId);
  const selected = requested >= 0 ? requested : Math.min(milestone, STAGE_IDS.length - 1);
  const stageId = STAGE_IDS[selected];
  const imagineView: ImagineView = params.get('imagine') === 'sketch' ? 'sketch' : 'studio';

  // A design turn runs with the tools of the stage on screen (FORGE-825).
  useEffect(() => {
    setStage(projectId, stageId ?? null);
  }, [projectId, setStage, stageId]);

  const collapsed = useCreateLayoutStore((s) => s.collapsed);
  const chatWidth = useCreateLayoutStore((s) => s.chatWidth);
  const setCollapsed = useCreateLayoutStore((s) => s.setCollapsed);

  const go = useCallback(
    (stage: StageId, imagine?: ImagineView) => {
      const next = new URLSearchParams(params);
      next.set('stage', stage);
      if (imagine) next.set('imagine', imagine);
      setParams(next);
    },
    [params, setParams],
  );

  /** Open the conversation with a request ready to send, and put the cursor in it. */
  const ask = useCallback(
    (text: string, intent?: Intent) => {
      if (text) {
        const draft = useCreateStore.getState().session(projectId).draft.trim();
        setDraft(projectId, draft ? `${text} ${draft}` : text);
      }
      if (intent) setIntent(projectId, intent);
      setCollapsed('chat', false);
      window.setTimeout(() => document.querySelector<HTMLTextAreaElement>(COMPOSER)?.focus(), 0);
    },
    [projectId, setCollapsed, setDraft, setIntent],
  );

  /** Send a design request in a stage now, and show the conversation it runs in. */
  const startIn = useCallback(
    (stage: StageId, text: string) => {
      setCollapsed('chat', false);
      sendNow(projectId, text, 'design', tools, stage);
    },
    [projectId, sendNow, setCollapsed, tools],
  );

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
  const chatCollapsed = collapsed.chat;

  return (
    <div className="cv-root">
      <header className="cv-head">
        <div className="cv-title">
          <Link to="/create" className="cv-back" aria-label="All creations">
            <ChevronLeft size={16} />
          </Link>
          <h1>{project.data?.name ?? 'Loading…'}</h1>
        </div>
        {/* One compact status spot: decisions waiting, and what is running. */}
        <div className="cv-status" data-testid="create-status">
          <div className="cv-decisions">
            <button
              type="button"
              className="cv-chip-btn"
              aria-expanded={decisionsOpen}
              aria-haspopup="dialog"
              aria-label={pendingCount > 0 ? `Decisions, ${pendingCount} waiting` : 'Decisions'}
              onClick={() => setDecisionsOpen((o) => !o)}
            >
              <ClipboardCheck size={14} aria-hidden="true" /> Decisions
              {pendingCount > 0 && (
                <span className="cv-count" data-testid="decisions-badge" title={`${pendingCount} waiting for you`}>
                  {pendingCount}
                </span>
              )}
            </button>
            {decisionsOpen && (
              <div className="cv-popover" role="dialog" aria-label="Decisions">
                <button type="button" className="cv-icon-btn cv-popover-close" aria-label="Close decisions" onClick={() => setDecisionsOpen(false)}>
                  <X size={14} />
                </button>
                <DecisionsPanel projectId={projectId} />
              </div>
            )}
          </div>
          <div className="cv-activity-slot" data-testid="create-activity-slot">
            <ActivityOverlay
              projectId={projectId}
              className="act-dock-create"
              onAction={operationAction}
              onApproval={() => setDecisionsOpen(true)}
            />
          </div>
        </div>
      </header>

      <JourneyRail
        milestone={milestone}
        selected={selected}
        onSelect={(i) => go(STAGE_IDS[i] ?? "imagine")}
        summaries={summaries}
        active={working}
      />

      <div
        className={`cv-body${chatCollapsed ? ' is-chat-collapsed' : ''}`}
        style={{ '--cv-chat-w': `${chatWidth}px` } as CSSProperties}
      >
        {chatCollapsed ? (
          <aside className="cv-rail" aria-label="Conversation" data-testid="chat-rail">
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
            <button
              type="button"
              className="cv-icon-btn cv-collapse"
              aria-label="Collapse the conversation"
              title="Collapse the conversation"
              onClick={() => setCollapsed('chat', true)}
            >
              <PanelLeftClose size={14} />
            </button>
            <CreateConversation projectId={projectId} tools={tools} />
            <CreateComposer projectId={projectId} tools={tools} busy={working} />
            <ChatResizer />
          </aside>
        )}

        <main
          className="cv-main"
          id="cv-stage-panel"
          role="tabpanel"
          aria-labelledby={`cv-stage-tab-${selected}`}
          data-stage={stageId}
        >
          {stageId === 'imagine' ? (
            <ImagineStage
              projectId={projectId}
              nodes={projectNodes}
              view={imagineView}
              onView={(v) => go('imagine', v)}
              onAskAssistant={() => startIn('imagine', START_IMAGINE_TEXT)}
              onAsk={(text) => ask(text, 'design')}
              onStartDeveloping={() => {
                go('develop');
                startIn('develop', START_DEVELOP_TEXT);
              }}
            />
          ) : stageId === 'develop' ? (
            <DevelopStage
              projectId={projectId}
              nodes={projectNodes}
              view={view.data}
              selectedId={session.selectedNodeId}
              onSelect={(id) => selectNode(projectId, id)}
              working={working}
              engineeringChecks={engineeringChecks}
              onAsk={(text) => ask(text, text.startsWith('Build an exact') ? 'design' : undefined)}
              onSketchFix={(fix) => {
                openDrawer(projectId, fix);
                go('imagine', 'sketch');
              }}
            />
          ) : stageId === 'check' ? (
            <CheckStage projectId={projectId} onAsk={(text) => ask(text)} />
          ) : (
            <MakeStage projectId={projectId} view={view.data} nodes={projectNodes} model={model} modelRevision={modelRevision} onAsk={(text) => ask(text)} />
          )}
          {view.isError && <p className="cv-error">Where the creation stands could not be loaded: {refusalOf(view.error)}</p>}
        </main>
      </div>
    </div>
  );
}
