import { Link, useParams } from 'react-router-dom';
import { Card } from '../components/ui/Card';
import { StatusBadge } from '../components/shared/StatusBadge';
import { EmptyState } from '../components/ui/EmptyState';
import { formatRelativeTime } from '../utils/format-time';
import { useSession } from '../hooks/use-sessions';
import { useScopedChat } from '../hooks/use-scoped-chat';
import { SessionChatPanel } from '../components/chat/integrations/SessionChatPanel';
import type { AgentEvent } from '../types/session';

const EVENT_ICONS: Record<AgentEvent['type'], string> = {
  task_started: '\u25B6',
  task_completed: '\u2713',
  task_failed: '\u2717',
  proposal_created: '\u25C6',
};

const EVENT_COLORS: Record<AgentEvent['type'], string> = {
  task_started: 'text-blue-500',
  task_completed: 'text-green-500',
  task_failed: 'text-red-500',
  proposal_created: 'text-amber-500',
};

export function SessionDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: session, isLoading } = useSession(id);

  const chat = useScopedChat({
    scopeKind: 'session',
    entityId: id ?? '',
  });

  if (isLoading) {
    return <div className="text-sm text-zinc-500">Loading session...</div>;
  }

  if (!session) {
    return (
      <EmptyState
        title="Session not found"
        description="The session you're looking for doesn't exist."
      />
    );
  }

  return (
    <div>
      <div className="mb-1">
        <Link
          to="/sessions"
          className="text-sm text-blue-600 hover:underline dark:text-blue-400"
        >
          &larr; Sessions
        </Link>
      </div>

      <div className="mb-6 flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-full bg-zinc-100 text-xs font-bold text-zinc-700 dark:bg-zinc-700 dark:text-zinc-300">
          {session.agentCode}
        </div>
        <div>
          <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">
            {session.taskType.replace(/_/g, ' ')}
          </h2>
          <span className="text-xs text-zinc-400">
            Started {formatRelativeTime(session.startedAt)}
          </span>
        </div>
        <StatusBadge status={session.status} />
      </div>

      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <Card>
          <div className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
            {session.id}
          </div>
          <div className="text-xs text-zinc-500">Session ID</div>
        </Card>
        <Card>
          <div className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
            {session.runId ?? '\u2014'}
          </div>
          <div className="text-xs text-zinc-500">Run ID</div>
        </Card>
        <Card>
          <div className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
            {session.completedAt ? formatRelativeTime(session.completedAt) : '\u2014'}
          </div>
          <div className="text-xs text-zinc-500">Completed</div>
        </Card>
      </div>

      <h3 className="mb-3 text-lg font-medium text-zinc-900 dark:text-zinc-100">
        Timeline
      </h3>

      {session.events.length === 0 ? (
        <EmptyState title="No events" description="No events have been recorded yet." />
      ) : (
        <div className="relative space-y-0 border-l-2 border-zinc-200 pl-6 dark:border-zinc-700">
          {session.events.map((event) => (
            <div key={event.id} className="relative pb-6 last:pb-0">
              <span
                className={`absolute -left-[1.625rem] flex h-5 w-5 items-center justify-center rounded-full bg-white text-xs dark:bg-zinc-900 ${EVENT_COLORS[event.type]}`}
              >
                {EVENT_ICONS[event.type]}
              </span>
              <div className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
                {event.message}
              </div>
              <div className="text-xs text-zinc-400">
                {formatRelativeTime(event.timestamp)}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Session Chat Panel */}
      <div className="mt-6">
        <SessionChatPanel
          sessionId={session.id}
          sessionTitle={session.taskType.replace(/_/g, ' ')}
          thread={chat.thread}
          messages={chat.messages}
          isTyping={chat.isTyping}
          onSendMessage={chat.sendMessage}
          onCreateThread={chat.createThread}
        />
      </div>
    </div>
  );
}
