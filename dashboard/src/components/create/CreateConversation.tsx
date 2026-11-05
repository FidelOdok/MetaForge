import { Fragment, useEffect, useRef } from 'react';
import { Loader2, RotateCcw, Pencil, X } from 'lucide-react';
import { useTwinChatThread, useTwinChatThreads } from '../../hooks/use-twin-chat';
import { displayText, intentOf, useCreateStore, type Intent } from '../../store/create-store';
import { useProjectOperations } from '../../store/operations-store';
import { MessageBody } from '../chat/MessageBody';
import { TurnInProgress } from '../chat/TurnInProgress';
import { useTurnStream } from '../chat/useTurnStream';
import { useQueryClient } from '@tanstack/react-query';
import { appearanceQueryKey } from '../../hooks/use-appearance';
import { plainClassificationLine } from '../../lib/appearance';
import { ChangeClassificationLine, PendingClassificationLine } from './ChangeClassificationLine';

const TURN_LABEL: Record<Intent, string> = {
  ask: 'Question',
  change: 'Change request',
  design: 'Design pass',
};

// What the agent's reply is, given what was asked: a proposal waits for a
// decision, a design pass reports what it actually did, a question is talk.
const REPLY_LABEL: Record<Intent, string> = {
  ask: 'Discussion',
  change: 'Proposed change · awaiting your decision',
  design: 'Executed with tools · see the result',
};

interface CreateConversationProps {
  projectId: string;
  tools: Record<Intent, string[]>;
}

export function CreateConversation({ projectId, tools }: CreateConversationProps) {
  const session = useCreateStore((s) => s.session(projectId));
  const setThread = useCreateStore((s) => s.setThread);
  const retry = useCreateStore((s) => s.retry);
  const restore = useCreateStore((s) => s.restore);
  const discard = useCreateStore((s) => s.discard);
  const reply = useCreateStore((s) => s.reply);
  const selectNode = useCreateStore((s) => s.selectNode);
  const busy = session.queue.some((i) => i.state === 'sending');
  // RT-001: a turn is acknowledged when the gateway's `accepted` event for its
  // operation arrives, before the reply does (FORGE-788).
  const operations = useProjectOperations(projectId);

  const threads = useTwinChatThreads(projectId);
  // The creation's conversation is the one it last used, else the project's latest.
  const threadId =
    session.threadId ??
    [...(threads.data ?? [])].sort((a, b) => (b.last_message_at ?? '').localeCompare(a.last_message_at ?? ''))[0]?.id ??
    null;
  useEffect(() => {
    if (threadId && threadId !== session.threadId) setThread(projectId, threadId);
  }, [projectId, threadId, session.threadId, setThread]);

  const thread = useTwinChatThread(projectId, threadId, busy);
  const sending = session.queue.find((i) => i.state === 'sending');
  // When each instruction started sending, for the thinking bubble's timer.
  const startedAt = useRef(new Map<string, number>());
  if (sending && !startedAt.current.has(sending.id)) startedAt.current.set(sending.id, Date.now());
  // Fetch the reply the moment the turn ends, not at the next poll: the
  // conversation sat idle for up to ten seconds with the answer already saved.
  const refetch = thread.refetch;
  const events = useTurnStream(threadId, !!sending, () => void refetch());
  const wasBusy = useRef(busy);
  const queryClient = useQueryClient();
  useEffect(() => {
    if (wasBusy.current && !busy) {
      void refetch();
      // An appearance turn changed the look: show it on the stage now.
      void queryClient.invalidateQueries({ queryKey: appearanceQueryKey(projectId) });
    }
    wasBusy.current = busy;
  }, [busy, refetch, queryClient, projectId]);
  // The server stores the message as soon as it arrives; while it is still
  // being worked on, show it once, as the instruction with its state.
  const pendingTexts = new Set(session.queue.filter((i) => i.state !== 'failed').map((i) => i.text.trim()));
  const messages = (thread.data?.messages ?? []).filter(
    (m, i, all) =>
      !(
        m.actor_kind === 'user' &&
        pendingTexts.has(displayText(m.content).trim()) &&
        !all.slice(i + 1).some((later) => later.actor_kind !== 'user')
      ),
  );
  // Scroll the conversation itself, never the page: scrollIntoView also moves
  // the window, which on a phone skipped past the product to the chat.
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = list.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, session.queue.length, events.length]);

  // Between the turn ending and the reply being fetched, the newest message is
  // still the person's own: keep the bubble up rather than go quiet.
  const last = messages[messages.length - 1];
  const awaitingReply =
    !sending &&
    session.queue.length === 0 &&
    last?.actor_kind === 'user' &&
    Date.now() - Date.parse(last.created_at) < 120_000;

  let lastIntent: Intent = 'ask';
  return (
    <div ref={list} className="cv-conversation" aria-live="polite">
      {messages.length === 0 && session.queue.length === 0 && (
        <p className="cv-muted cv-conversation-empty">
          Your conversation appears here. Describe the product in your own words; questions come when they matter.
        </p>
      )}
      {messages.map((m, index) => {
        const mine = m.actor_kind === 'user';
        if (mine) lastIntent = intentOf(m.content);
        const label = mine ? TURN_LABEL[intentOf(m.content)] : REPLY_LABEL[lastIntent];
        return (
          <article key={m.id} className={`cv-msg ${mine ? 'is-mine' : 'is-agent'} ${m.status === 'failed' ? 'is-failed' : ''}`}>
            <header>
              <span className="cv-msg-who">{mine ? 'You' : 'MetaForge'}</span>
              <span className={`cv-tag cv-tag-${mine ? 'turn' : lastIntent}`}>{label}</span>
              {m.graph_ref_label && <span className="cv-tag">About: {m.graph_ref_label}</span>}
            </header>
            <MessageBody
              text={displayText(m.content)}
              blocks={m.blocks}
              plain={mine}
              projectId={projectId}
              interactive={!busy && session.queue.length === 0 && index === messages.length - 1}
              onReply={(answer) => reply(projectId, answer, tools)}
              onShowPart={(nodeId) => selectNode(projectId, nodeId)}
            />
            {mine && <ChangeClassificationLine projectId={projectId} classification={session.classifications?.[m.id]} />}
          </article>
        );
      })}
      {session.queue.map((ins) => (
        <Fragment key={ins.id}>
          <article className={`cv-msg is-mine is-${ins.state}`}>
            <header>
              <span className="cv-msg-who">You</span>
              <span className="cv-tag cv-tag-turn">{TURN_LABEL[ins.intent]}</span>
              <span className={`cv-state cv-state-${ins.state}`}>
                {ins.state === 'sending' ? (
                  <>
                    <Loader2 size={12} className="cv-spin" aria-hidden="true" />{' '}
                    {ins.operationId && operations.ops.byId[ins.operationId]
                      ? 'Received · working on it'
                      : 'Sending…'}
                  </>
                ) : ins.state === 'queued' ? (
                  'Queued · sent when the current pass ends'
                ) : (
                  'Not sent'
                )}
              </span>
            </header>
            <p>{ins.text}</p>
            {ins.state === 'sending' && ins.operationId && (
              <PendingClassificationLine
                line={plainClassificationLine(
                  operations.ops.byId[ins.operationId]?.milestones.find((m) => m.stageId === 'classified')?.label,
                )}
              />
            )}
            {ins.state === 'failed' && (
              <footer className="cv-msg-actions">
                <span className="cv-error">{ins.error}</span>
                <button type="button" className="cv-link" onClick={() => retry(projectId, ins.id, tools)}>
                  <RotateCcw size={12} /> Retry
                </button>
                <button type="button" className="cv-link" onClick={() => restore(projectId, ins.id)}>
                  <Pencil size={12} /> Edit
                </button>
                <button type="button" className="cv-link" onClick={() => discard(projectId, ins.id)}>
                  <X size={12} /> Discard
                </button>
              </footer>
            )}
          </article>
          {ins.state === 'sending' && (
            <TurnInProgress events={events} startedAt={startedAt.current.get(ins.id) ?? Date.now()} />
          )}
        </Fragment>
      ))}
      {awaitingReply && last && <TurnInProgress events={events} startedAt={Date.parse(last.created_at)} />}
    </div>
  );
}
