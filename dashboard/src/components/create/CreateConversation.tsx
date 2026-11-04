import { useEffect, useRef } from 'react';
import { Loader2, RotateCcw, Pencil, X } from 'lucide-react';
import { useTwinChatThread, useTwinChatThreads } from '../../hooks/use-twin-chat';
import { displayText, intentOf, useCreateStore, type Intent } from '../../store/create-store';
import { MessageBody } from '../chat/MessageBody';

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
  const messages = thread.data?.messages ?? [];
  // Scroll the conversation itself, never the page: scrollIntoView also moves
  // the window, which on a phone skipped past the product to the chat.
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = list.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, session.queue.length]);

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
          </article>
        );
      })}
      {session.queue.map((ins) => (
        <article key={ins.id} className={`cv-msg is-mine is-${ins.state}`}>
          <header>
            <span className="cv-msg-who">You</span>
            <span className="cv-tag cv-tag-turn">{TURN_LABEL[ins.intent]}</span>
            <span className={`cv-state cv-state-${ins.state}`}>
              {ins.state === 'sending' ? (
                <>
                  <Loader2 size={12} className="cv-spin" aria-hidden="true" /> Being applied
                </>
              ) : ins.state === 'queued' ? (
                'Queued · sent when the current pass ends'
              ) : (
                'Not sent'
              )}
            </span>
          </header>
          <p>{ins.text}</p>
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
      ))}
    </div>
  );
}
