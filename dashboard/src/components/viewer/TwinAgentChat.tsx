import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import {
  ArrowUp,
  Box,
  ChevronDown,
  File as FileIcon,
  Folder,
  Gauge,
  GitPullRequest,
  Image as ImageIcon,
  MessageSquare,
  Mic,
  Paperclip,
  Plug,
  Plus,
  RefreshCw,
  SlidersHorizontal,
  Square,
  X,
} from 'lucide-react';
import { useHarnessModels, useHarnessProviders, useHarnessTools } from '../../hooks/use-harness';
import { assistantKeys, useDecideProposal, useProposals } from '../../hooks/use-assistant';
import { twinKeys } from '../../hooks/use-twin';
import { twinChatKeys, useCapturedSessions, useTwinChatThread, useTwinChatThreads } from '../../hooks/use-twin-chat';
import { createTwinChatThread, sendTwinChatMessage, twinChatStreamUrl } from '../../api/endpoints/twin-chat';
import { importWorkProduct } from '../../api/endpoints/twin';
import type { Proposal } from '../../api/endpoints/assistant';
import type { TwinNode } from '../../types/twin';
import { isSampleMode } from '../../lib/sample-workspace';

type Interaction = 'discuss' | 'propose' | 'tools';
type Verbosity = 'concise' | 'normal' | 'verbose';
type PanelTab = 'conversation' | 'changes';
type Popover = 'threads' | 'attach' | 'commands' | 'tools' | 'model' | 'effort' | 'verbosity' | 'context' | 'voice';

interface Attachment {
  key: string;
  file: File;
  nodeId?: string;
  error?: string;
}

interface ActivityEvent {
  kind: string;
  data: unknown;
  at: string;
}

const POPOVER_TITLES: Record<Popover, string> = {
  threads: 'Threads',
  attach: 'Add to this turn',
  commands: 'Commands',
  tools: 'Connectors and tools',
  model: 'Model for this turn',
  effort: 'Reasoning effort',
  verbosity: 'Show',
  context: 'Working context',
  voice: 'Voice input',
};

const COMMANDS: [string, string][] = [
  ['/explain', 'Explain the selected component and its engineering role.'],
  ['/validate', 'Create a validation plan for the selected design and its assumptions.'],
  ['/alternatives', 'Compare compatible alternatives and their trade-offs.'],
  ['/propose', 'Propose a reviewable change to the selected component.'],
];

const STREAM_EVENTS = ['agent.thinking', 'agent.action_started', 'agent.step', 'message.delta', 'context.stats', 'agent.done'];

// Strips the context suffixes this drawer appends to a user turn before display.
const CONTEXT_SUFFIX =
  /\n\nSelected twin object \(context snapshot\):|\n\nAttached twin work products|\nDiscuss using the supplied context\.|\nPropose a reviewable change using|\nUse only the explicitly selected tools/;

let attachmentSeq = 0;

export interface TwinAgentChatProps {
  projectId: string | null;
  projectName?: string;
  node?: TwinNode;
  onApplied: () => void;
  onEngage?: () => void;
  headerActions?: ReactNode;
}

/**
 * Twin agent drawer: project-scoped conversation threads plus the pending
 * change proposals for the project, docked over the digital-twin canvas.
 */
export function TwinAgentChat({ projectId, projectName, node, onApplied, onEngage, headerActions }: TwinAgentChatProps) {
  const queryClient = useQueryClient();
  const providers = useHarnessProviders();
  const tools = useHarnessTools();
  const [threadId, setThreadId] = useState<string | null>(null);
  const [freshThread, setFreshThread] = useState(false);
  const [draft, setDraft] = useState('');
  const [interaction, setInteraction] = useState<Interaction>('discuss');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [tab, setTab] = useState<PanelTab>('conversation');
  const [popover, setPopover] = useState<Popover | null>(null);
  const [provider, setProvider] = useState('');
  const [model, setModel] = useState('');
  const [verbosity, setVerbosity] = useState<Verbosity>('normal');
  const [selectedTools, setSelectedTools] = useState<string[]>([]);
  const [includeNode, setIncludeNode] = useState(true);
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [contextStats, setContextStats] = useState<Record<string, unknown> | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [reviewing, setReviewing] = useState<Proposal | null>(null);
  const [reviewNote, setReviewNote] = useState('');
  const decide = useDecideProposal();

  const models = useHarnessModels(provider || providers.data?.activeProvider || null);
  const captured = useCapturedSessions(projectId, popover === 'threads');
  const contextNode = includeNode ? node : undefined;

  const threads = useTwinChatThreads(projectId);
  const activeThreadId = threadId ?? (freshThread ? null : threads.data?.[0]?.id ?? null);
  const thread = useTwinChatThread(projectId, activeThreadId, busy);
  const proposals = useProposals(projectId ?? undefined);
  const pending = projectId
    ? (proposals.data?.proposals ?? []).filter((p) => p.status === 'pending' && p.project_id === projectId)
    : [];
  const proposeTool = tools.data?.find((t) => t.id === 'twin.propose_change');
  const effectiveProvider = provider || providers.data?.activeProvider;
  const canSend = !!(effectiveProvider && providers.data?.providers.some((p) => p.id === effectiveProvider && p.configured));
  const turnTools = interaction === 'propose' && proposeTool ? [proposeTool.id] : interaction === 'tools' ? selectedTools : [];
  const draftTokens = Math.ceil(draft.length / 4);

  useEffect(() => setIncludeNode(true), [node?.id]);

  useEffect(() => {
    folderInputRef.current?.setAttribute('webkitdirectory', '');
  }, []);

  // Close the open popover on an outside pointer or Escape.
  useEffect(() => {
    if (!popover) return;
    const onPointer = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setPopover(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setPopover(null);
        textareaRef.current?.focus();
      }
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [popover]);

  useEffect(() => () => abortRef.current?.abort(), []);

  // Live agent events for the open thread (SSE). Not available offline.
  useEffect(() => {
    if (!activeThreadId || isSampleMode() || typeof EventSource === 'undefined') return;
    const source = new EventSource(twinChatStreamUrl(activeThreadId));
    const refresh = () => {
      void queryClient.invalidateQueries({ queryKey: twinChatKeys.thread(projectId, activeThreadId) });
      void queryClient.invalidateQueries({ queryKey: assistantKeys.all });
    };
    source.addEventListener('message.created', refresh);
    source.addEventListener('agent.done', refresh);
    for (const kind of STREAM_EVENTS) {
      source.addEventListener(kind, (evt) => {
        const raw = (evt as MessageEvent).data;
        let data: unknown;
        try {
          data = JSON.parse(raw);
        } catch {
          data = raw;
        }
        if (kind === 'context.stats' && typeof data === 'object' && data !== null) {
          setContextStats(data as Record<string, unknown>);
        } else if (kind !== 'message.delta') {
          setActivity((prev) => [...prev.slice(-99), { kind, data, at: new Date().toISOString() }]);
        }
        if (kind === 'message.delta') refresh();
      });
    }
    return () => source.close();
  }, [activeThreadId, projectId, queryClient]);

  const toggle = (p: Popover) => setPopover((cur) => (cur === p ? null : p));

  function addFiles(list: FileList | null) {
    if (!list || busy) return;
    const files = Array.from(list);
    if (files.length + attachments.length > 20) {
      setNotice('Attach up to 20 files per turn. Choose a smaller folder or selection.');
      return;
    }
    if (files.some((f) => f.size > 100 * 1024 * 1024)) {
      setNotice('Each attachment must be 100 MB or smaller.');
      return;
    }
    setAttachments((prev) => [...prev, ...files.map((file) => ({ key: `att-${Date.now()}-${attachmentSeq++}`, file }))]);
    setPopover(null);
  }

  function newThread() {
    setThreadId(null);
    setFreshThread(true);
    setActivity([]);
    setContextStats(null);
    setTab('conversation');
    setPopover(null);
    setNotice('New thread. Your draft is retained.');
    textareaRef.current?.focus();
  }

  async function send() {
    if (busy || !projectId || !draft.trim() || !canSend || (interaction === 'propose' && !proposeTool)) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true);
    setNotice('');
    const refNode = contextNode;
    const text = draft.trim();
    try {
      const refs: { id: string; name: string }[] = [];
      for (const att of attachments) {
        if (att.nodeId) {
          refs.push({ id: att.nodeId, name: att.file.name });
          continue;
        }
        const form = new FormData();
        form.append('file', att.file);
        form.append('project_id', projectId);
        try {
          const imported = await importWorkProduct(form);
          refs.push({ id: imported.id, name: imported.name });
          setAttachments((prev) => prev.map((a) => (a.key === att.key ? { ...a, nodeId: imported.id, error: undefined } : a)));
          void queryClient.invalidateQueries({ queryKey: twinKeys.all });
        } catch {
          setAttachments((prev) => prev.map((a) => (a.key === att.key ? { ...a, error: 'Import failed' } : a)));
          setNotice(
            'Attachment import failed. Imported files remain in the twin; remove or retry the failed attachment before sending.',
          );
          return;
        }
        if (controller.signal.aborted) {
          setNotice('Stopped before sending. Imported files remain attached.');
          return;
        }
      }
      if (controller.signal.aborted) return;
      let target = activeThreadId;
      if (!target) {
        target = (await createTwinChatThread(projectId)).id;
        setThreadId(target);
        setFreshThread(false);
        void queryClient.invalidateQueries({ queryKey: twinChatKeys.threads(projectId) });
      }
      const snapshot = refNode
        ? `\n\nSelected twin object (context snapshot): ${JSON.stringify({
            id: refNode.id,
            name: refNode.name,
            type: refNode.type,
            domain: refNode.domain,
            status: refNode.status,
            properties: refNode.properties,
            updated_at: refNode.updatedAt,
          })}`
        : '';
      const instruction =
        interaction === 'propose'
          ? '\nPropose a reviewable change using twin.propose_change; do not apply it. Include rationale and affected work products.'
          : interaction === 'tools'
            ? '\nUse only the explicitly selected tools for this request. Report actions and results accurately.'
            : '\nDiscuss using the supplied context. No tools are enabled for this turn. Do not claim to have changed or simulated the design.';
      await sendTwinChatMessage(
        target,
        {
          content:
            text +
            snapshot +
            (refs.length
              ? '\n\nAttached twin work products (references, not inline image content): ' + JSON.stringify(refs)
              : '') +
            instruction,
          tools: turnTools,
          ...(provider ? { provider } : {}),
          ...(model.trim() ? { model: model.trim() } : {}),
          ...(refNode ? { graph_ref_node: refNode.id, graph_ref_type: refNode.type, graph_ref_label: refNode.name } : {}),
        },
        controller.signal,
      );
      setDraft('');
      setAttachments([]);
      await queryClient.invalidateQueries({ queryKey: twinChatKeys.all });
      await queryClient.invalidateQueries({ queryKey: assistantKeys.all });
      setNotice('Turn finished. Review the gateway response below.');
    } catch {
      setNotice(
        controller.signal.aborted
          ? 'Stop requested. Refresh the conversation to check what was recorded.'
          : 'The response could not be confirmed. Refresh the conversation before resending; your message may already have been recorded.',
      );
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  function openReview(p: Proposal) {
    setReviewing(p);
    setReviewNote('');
    decide.reset();
    dialogRef.current?.showModal?.();
  }

  function submitDecision(decision: 'approve' | 'reject') {
    if (!reviewing) return;
    decide.mutate(
      {
        changeId: reviewing.change_id,
        decision,
        reason: reviewNote.trim() || `${decision === 'approve' ? 'Approved' : 'Rejected'} after review in digital twin`,
        reviewer: 'dashboard-user',
      },
      {
        onSuccess: (result) => {
          dialogRef.current?.close?.();
          setNotice(`Proposal ${result.status}. Check the refreshed twin for the apply result.`);
          void queryClient.invalidateQueries({ queryKey: assistantKeys.all });
          void queryClient.invalidateQueries({ queryKey: twinKeys.all });
          if (decision === 'approve') onApplied();
        },
      },
    );
  }

  const messages = thread.data?.messages ?? [];

  return (
    <aside ref={rootRef} className="twin-agent sample-chat" aria-label="Digital twin agent">
      <div className="sc-head">
        <button className="sc-thread-button" aria-expanded={popover === 'threads'} onClick={() => toggle('threads')}>
          <MessageSquare size={15} />
          <strong>{thread.data?.title || 'New conversation'}</strong>
          <ChevronDown size={13} />
        </button>
        <span className="sc-route">{node?.domain || 'engineering'} agent</span>
        {headerActions}
        <button className="sc-icon" title="New thread · retain history" disabled={busy} onClick={newThread}>
          <Plus size={15} />
        </button>
      </div>

      <div className="sc-tabs" role="group" aria-label="Agent panel view">
        <button
          aria-pressed={tab === 'conversation'}
          onClick={() => {
            setTab('conversation');
            onEngage?.();
          }}
        >
          Conversation
        </button>
        <button
          aria-pressed={tab === 'changes'}
          onClick={() => {
            setTab('changes');
            onEngage?.();
          }}
        >
          <GitPullRequest size={13} />
          Changes · {pending.length}
        </button>
        <button
          className="sc-refresh"
          title="Refresh conversation"
          onClick={() => {
            void threads.refetch();
            if (activeThreadId) void thread.refetch();
          }}
        >
          <RefreshCw size={13} />
        </button>
      </div>

      {tab === 'conversation' ? (
        <div className="sc-stream" aria-label="Conversation messages" aria-busy={busy}>
          {!projectId ? (
            <p>Choose a project to begin a conversation.</p>
          ) : threads.isError || thread.isError ? (
            <p role="alert">
              Conversation unavailable. <Link to="/settings">Check the gateway connection.</Link>
            </p>
          ) : thread.isLoading && activeThreadId ? (
            <p role="status">Loading conversation…</p>
          ) : messages.length ? (
            messages.map((m) => (
              <article key={m.id} className={`sc-turn ${m.actor_kind === 'user' ? 'sc-user' : 'sc-agent'}`}>
                <header>
                  <strong>
                    {m.actor_kind === 'user' ? 'You' : m.actor_kind === 'agent' ? 'MetaForge agent' : m.actor_kind}
                  </strong>
                  <time>{new Date(m.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time>
                </header>
                <p>{m.content.split(CONTEXT_SUFFIX)[0]}</p>
                {m.graph_ref_label && (
                  <span className="sc-reference">
                    <Box size={12} />
                    {m.graph_ref_label}
                  </span>
                )}
              </article>
            ))
          ) : (
            <div className="sc-welcome">
              <p>Ask about a part, inspect evidence, or describe a change.</p>
              <div>
                {COMMANDS.slice(0, 3).map(([cmd, prompt]) => (
                  <button
                    key={cmd}
                    onClick={() => {
                      setDraft(prompt);
                      textareaRef.current?.focus();
                    }}
                  >
                    {cmd}
                  </button>
                ))}
              </div>
            </div>
          )}
          {verbosity !== 'concise' && activity.length > 0 && (
            <details className="sc-activity" open={verbosity === 'verbose'}>
              <summary>Agent activity · {activity.length} events</summary>
              {activity.map((evt, i) => (
                <div key={i}>
                  <strong>{evt.kind}</strong>
                  <time>{new Date(evt.at).toLocaleTimeString()}</time>
                  {verbosity === 'verbose' ? (
                    <pre>{JSON.stringify(evt.data, null, 2)}</pre>
                  ) : (
                    <p>
                      {typeof evt.data === 'string'
                        ? evt.data
                        : typeof evt.data === 'object' && evt.data !== null
                          ? String(
                              (evt.data as { message?: unknown; tool?: unknown }).message ||
                                (evt.data as { tool?: unknown }).tool ||
                                'Event received',
                            )
                          : ''}
                    </p>
                  )}
                </div>
              ))}
            </details>
          )}
          {busy && <p role="status">Agent turn in progress…</p>}
        </div>
      ) : (
        <div className="sc-stream sc-changes">
          {proposals.isError ? (
            <p role="alert">
              Proposals unavailable. <button onClick={() => void proposals.refetch()}>Retry</button>
            </p>
          ) : pending.length ? (
            pending.map((p) => (
              <article key={p.change_id}>
                <span>Needs review</span>
                <h3>{p.description}</h3>
                <p>{p.work_products_affected.length} affected work products</p>
                <button onClick={() => openReview(p)}>Inspect proposal ↗</button>
              </article>
            ))
          ) : (
            <p>No pending changes.</p>
          )}
          <Link to="/approvals">All approvals ↗</Link>
        </div>
      )}

      <form
        className="sc-dock"
        onSubmit={(e) => {
          e.preventDefault();
          onEngage?.();
          setTab('conversation');
          void send();
        }}
      >
        {(contextNode || attachments.length > 0) && (
          <div className="sc-attachments">
            {contextNode && (
              <span>
                <Box size={12} />
                {contextNode.name}
                <button type="button" aria-label="Remove selected object from context" onClick={() => setIncludeNode(false)}>
                  <X size={12} />
                </button>
              </span>
            )}
            {attachments.map((att) => (
              <span key={att.key} data-error={!!att.error}>
                <Paperclip size={12} />
                {att.file.name}
                <small>{att.error || (att.nodeId ? 'Imported' : 'Pending import')}</small>
                <button
                  type="button"
                  disabled={busy}
                  aria-label={`Remove ${att.file.name}`}
                  onClick={() => setAttachments((prev) => prev.filter((a) => a.key !== att.key))}
                >
                  <X size={12} />
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="sc-input-row">
          <button type="button" className="sc-scope" title="Inspect context" onClick={() => toggle('context')}>
            {contextNode ? 'node' : projectName || 'project'}
          </button>
          <label className="sr-only" htmlFor="twin-agent-message">
            Message to engineering agent
          </label>
          <textarea
            id="twin-agent-message"
            ref={textareaRef}
            rows={1}
            value={draft}
            disabled={!projectId || busy}
            placeholder="Ask about this node, or describe a change"
            onChange={(e) => {
              setDraft(e.target.value);
              if (e.target.value === '/') setPopover('commands');
            }}
            onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                e.preventDefault();
                onEngage?.();
                void send();
              }
            }}
          />
          {busy ? (
            <button className="sc-send" type="button" aria-label="Stop request" onClick={() => abortRef.current?.abort()}>
              <Square size={15} />
            </button>
          ) : (
            <button
              className="sc-send"
              type="submit"
              aria-label="Send message"
              disabled={!projectId || !draft.trim() || !canSend || (interaction === 'propose' && !proposeTool)}
            >
              <ArrowUp size={17} />
            </button>
          )}
        </div>
        <div className="sc-toolbar">
          <button
            type="button"
            className="sc-icon"
            title="Add images, files or folders"
            aria-expanded={popover === 'attach'}
            onClick={() => toggle('attach')}
          >
            <Plus size={16} />
          </button>
          <button type="button" className="sc-icon" title="Slash commands" onClick={() => toggle('commands')}>
            /
          </button>
          <button type="button" className="sc-icon" title="Connectors and tools" onClick={() => toggle('tools')}>
            <Plug size={15} />
            {turnTools.length > 0 && <sup>{turnTools.length}</sup>}
          </button>
          <span className="sc-divider" />
          <button type="button" className="sc-setting" title="Model for this turn" onClick={() => toggle('model')}>
            {model || providers.data?.activeModel || 'Model'}
            <ChevronDown size={11} />
          </button>
          <button type="button" className="sc-setting" title="Reasoning effort" onClick={() => toggle('effort')}>
            <Gauge size={13} />
            default
          </button>
          <button type="button" className="sc-setting" title="Response detail" onClick={() => toggle('verbosity')}>
            <SlidersHorizontal size={13} />
            {verbosity}
          </button>
          <button type="button" className="sc-context-meter" title="Context usage" onClick={() => toggle('context')}>
            {contextStats ? 'Context stats' : `~${draftTokens} draft tokens`}
          </button>
          <button type="button" className="sc-icon" title="Voice input availability" onClick={() => toggle('voice')}>
            <Mic size={15} />
          </button>
        </div>
        {!canSend && (
          <p className="sc-help">
            <Link to="/settings">Connect a provider and model</Link> to send a message.
          </p>
        )}
      </form>

      <input
        type="file"
        ref={fileInputRef}
        hidden
        multiple
        onChange={(e) => {
          addFiles(e.target.files);
          e.target.value = '';
        }}
      />
      <input
        type="file"
        ref={folderInputRef}
        hidden
        multiple
        onChange={(e) => {
          addFiles(e.target.files);
          e.target.value = '';
        }}
      />

      {popover && (
        <section className="sc-popover" aria-label={`${popover} options`}>
          <header>
            <strong>{POPOVER_TITLES[popover]}</strong>
            <button className="sc-icon" aria-label="Close chat options" onClick={() => setPopover(null)}>
              <X size={14} />
            </button>
          </header>

          {popover === 'threads' && (
            <>
              <div className="sc-pop-list">
                {threads.isLoading ? (
                  <p>Loading threads…</p>
                ) : threads.isError ? (
                  <p>Threads unavailable.</p>
                ) : (
                  (threads.data ?? []).map((t) => (
                    <button
                      key={t.id}
                      disabled={busy}
                      onClick={() => {
                        setThreadId(t.id);
                        setFreshThread(false);
                        setContextStats(null);
                        setActivity([]);
                        setPopover(null);
                        setTab('conversation');
                        onEngage?.();
                      }}
                    >
                      <strong>{t.title}</strong>
                      <small>{new Date(t.last_message_at).toLocaleString()}</small>
                    </button>
                  ))
                )}
                <button disabled={!projectId || busy} onClick={newThread}>
                  <Plus size={14} />
                  New thread
                </button>
              </div>
              <h4>Captured from MCP clients</h4>
              {captured.isLoading ? (
                <p>Loading captured sessions…</p>
              ) : captured.isError ? (
                <p>Captured sessions unavailable.</p>
              ) : captured.data?.length ? (
                captured.data.slice(0, 8).map((s) => (
                  <Link key={s.id} className="sc-captured" to={`/sessions/${encodeURIComponent(s.id)}`}>
                    <strong>
                      {s.agentCode} · {s.taskType}
                    </strong>
                    <small>
                      {s.status} · {s.events.length} events · read only
                    </small>
                  </Link>
                ))
              ) : (
                <p>No captured sessions for this project.</p>
              )}
              <Link to="/sessions">Full session history ↗</Link>
            </>
          )}

          {popover === 'attach' && (
            <>
              <div className="sc-pop-list">
                <button
                  onClick={() => {
                    if (fileInputRef.current) fileInputRef.current.accept = 'image/*';
                    fileInputRef.current?.click();
                  }}
                >
                  <ImageIcon size={17} />
                  <span>
                    Image<small>Import a reference image</small>
                  </span>
                </button>
                <button
                  onClick={() => {
                    if (fileInputRef.current) fileInputRef.current.accept = '';
                    fileInputRef.current?.click();
                  }}
                >
                  <FileIcon size={17} />
                  <span>
                    File<small>CAD, schematic, datasheet or evidence</small>
                  </span>
                </button>
                <button onClick={() => folderInputRef.current?.click()}>
                  <Folder size={17} />
                  <span>
                    Folder<small>Choose up to 20 files</small>
                  </span>
                </button>
              </div>
              <p>
                Files are imported to this project's twin when you send. The message receives work-product references; image
                understanding depends on the agent's tools. Removing a chip does not delete an imported file.
              </p>
            </>
          )}

          {popover === 'commands' && (
            <>
              <div className="sc-pop-list">
                {COMMANDS.map(([cmd, prompt]) => (
                  <button
                    key={cmd}
                    onClick={() => {
                      setDraft(prompt);
                      if (cmd === '/propose') setInteraction('propose');
                      setPopover(null);
                      textareaRef.current?.focus();
                    }}
                  >
                    <strong>{cmd}</strong>
                    <small>{prompt}</small>
                  </button>
                ))}
              </div>
              <p>Prompt shortcuts. No command runs until you send.</p>
            </>
          )}

          {popover === 'tools' && (
            <>
              <label>
                Interaction
                <select value={interaction} onChange={(e) => setInteraction(e.target.value as Interaction)} disabled={busy}>
                  <option value="discuss">Discuss · no tools</option>
                  <option value="propose" disabled={!proposeTool}>
                    Propose · reviewable changes
                  </option>
                  <option value="tools">Use selected tools</option>
                </select>
              </label>
              <p>
                {interaction === 'tools'
                  ? 'Selected tools may perform actions requested in your message.'
                  : interaction === 'propose'
                    ? 'Only twin.propose_change is enabled.'
                    : 'Tools are disabled for this turn.'}{' '}
                Requires a harness-enabled gateway.
              </p>
              {tools.isError ? (
                <p>Tool registry unavailable.</p>
              ) : tools.isLoading ? (
                <p>Loading tools…</p>
              ) : (
                [...new Set((tools.data ?? []).map((t) => t.server))].map((server) => (
                  <div key={server} className="sc-tool-group">
                    <h4>{server}</h4>
                    {tools.data
                      ?.filter((t) => t.server === server)
                      .map((t) => (
                        <label key={t.id}>
                          <input
                            type="checkbox"
                            disabled={busy || interaction !== 'tools'}
                            checked={turnTools.includes(t.id)}
                            onChange={(e) =>
                              setSelectedTools((prev) => (e.target.checked ? [...prev, t.id] : prev.filter((id) => id !== t.id)))
                            }
                          />
                          <span>
                            {t.name}
                            <small>{t.capability || t.id}</small>
                          </span>
                        </label>
                      ))}
                  </div>
                ))
              )}
              {!tools.isLoading && !tools.data?.length && <p>No tools reported by the gateway.</p>}
            </>
          )}

          {popover === 'model' && (
            <>
              <label>
                Provider
                <select
                  value={provider}
                  disabled={busy}
                  onChange={(e) => {
                    setProvider(e.target.value);
                    setModel('');
                  }}
                >
                  <option value="">Gateway default · {providers.data?.activeProvider || 'unconfigured'}</option>
                  {providers.data?.providers.map((p) => (
                    <option key={p.id} value={p.id} disabled={!p.configured}>
                      {p.id}
                      {p.configured ? '' : ' · not configured'}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Model
                <input
                  list="sc-model-list"
                  value={model}
                  disabled={busy}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder={providers.data?.activeModel || 'Provider default'}
                />
                <datalist id="sc-model-list">
                  {models.data?.map((m) => <option key={m} value={m} />)}
                </datalist>
              </label>
              {models.isError && <p>Model list unavailable. Enter a supported model ID.</p>}
              <p>Overrides this turn only. Gateway defaults stay unchanged.</p>
            </>
          )}

          {popover === 'effort' && (
            <>
              <div className="sc-pop-list">
                {['low', 'medium', 'high'].map((level) => (
                  <button key={level} disabled>
                    {level}
                    <small>Not exposed by the current chat API</small>
                  </button>
                ))}
              </div>
              <p>Uses the provider's default. No effort override is sent.</p>
            </>
          )}

          {popover === 'verbosity' && (
            <div className="sc-pop-list">
              {(['concise', 'normal', 'verbose'] as const).map((level) => (
                <button
                  key={level}
                  aria-pressed={verbosity === level}
                  onClick={() => {
                    setVerbosity(level);
                    setPopover(null);
                    onEngage?.();
                  }}
                >
                  <strong>{level}</strong>
                  <small>
                    {level === 'concise'
                      ? 'Answers and proposals'
                      : level === 'normal'
                        ? 'Plus reported agent activity'
                        : 'Plus raw received events and timestamps'}
                  </small>
                </button>
              ))}
            </div>
          )}

          {popover === 'context' && (
            <>
              <p>
                <strong>{projectName || 'No project selected'}</strong>
              </p>
              {node && (
                <label className="sc-check">
                  <input type="checkbox" checked={includeNode} onChange={(e) => setIncludeNode(e.target.checked)} />
                  Include {node.name}
                </label>
              )}
              <p>
                {attachments.length} attachment references · approximately {draftTokens} draft tokens (character estimate).
              </p>
              {contextStats ? (
                <>
                  <h4>Latest gateway context stats</h4>
                  <pre>{JSON.stringify(contextStats, null, 2)}</pre>
                </>
              ) : (
                <p>The gateway has not reported context-window usage for this thread.</p>
              )}
            </>
          )}

          {popover === 'voice' && (
            <p>
              Voice transcription is not exposed by the connected chat API. You can use your device's dictation in the
              message field.
            </p>
          )}
        </section>
      )}

      {notice && (
        <p className="sc-notice" role="status">
          {notice}
        </p>
      )}

      <dialog ref={dialogRef} className="project-dialog agent-review-dialog" aria-labelledby="proposal-title">
        <div className="flow-form">
          <div className="section-heading">
            <h2 id="proposal-title">Review proposed change</h2>
            <button
              className="icon-control"
              aria-label="Close proposal"
              onClick={() => dialogRef.current?.close?.()}
              disabled={decide.isPending}
            >
              <X size={20} />
            </button>
          </div>
          <p>{reviewing?.description}</p>
          <h3>Affected work products</h3>
          <ul>
            {reviewing?.work_products_affected.map((wp) => (
              <li key={wp} className="mono-value">
                {wp}
              </li>
            ))}
          </ul>
          <details open>
            <summary>Proposed diff</summary>
            <pre>{JSON.stringify(reviewing?.diff, null, 2)}</pre>
          </details>
          <label htmlFor="proposal-reason">Review note</label>
          <textarea id="proposal-reason" rows={2} value={reviewNote} onChange={(e) => setReviewNote(e.target.value)} />
          <p className="field-help">
            Approval invokes the gateway’s apply handler. Verify the refreshed artifact and validation evidence afterwards.
          </p>
          {decide.isError && (
            <p className="form-error" role="alert">
              Decision could not be confirmed. Refresh the proposals before retrying.
            </p>
          )}
          <div className="dialog-actions">
            <button className="action-secondary" disabled={decide.isPending} onClick={() => submitDecision('reject')}>
              Reject
            </button>
            <button className="action-primary" disabled={decide.isPending} onClick={() => submitDecision('approve')}>
              {decide.isPending ? 'Submitting…' : 'Approve & apply'}
            </button>
          </div>
        </div>
      </dialog>
    </aside>
  );
}
