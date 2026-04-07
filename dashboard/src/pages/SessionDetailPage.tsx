import { Link, useParams } from 'react-router-dom';
import { StatusBadge } from '../components/shared/StatusBadge';
import { EmptyState } from '../components/ui/EmptyState';
import { formatRelativeTime } from '../utils/format-time';
import { useSession } from '../hooks/use-sessions';
import { useScopedChat } from '../hooks/use-scoped-chat';
import { SessionChatPanel } from '../components/chat/integrations/SessionChatPanel';
import type { AgentEvent, AgentSession } from '../types/session';

// KC color tokens
const KC = {
  surfaceContainer: 'rgba(30,31,38,0.85)',
  surfaceHigh: '#282a30',
  surfaceLowest: '#0a0b10',
  surfaceBorder: 'rgba(65,72,90,0.2)',
  surfaceBorderFaint: 'rgba(65,72,90,0.08)',
  onSurface: '#e2e2eb',
  onSurfaceVariant: '#9a9aaa',
  primary: '#ffb783',
  running: '#e67e22',
  done: '#3dd68c',
  pending: '#9a9aaa',
  error: '#ffb4ab',
  info: '#86cfff',
  warning: '#f59e0b',
} as const;

function statusDotColor(status: AgentSession['status']): string {
  switch (status) {
    case 'running': return KC.running;
    case 'completed': return KC.done;
    case 'failed': return KC.error;
    case 'pending': return KC.pending;
    default: return KC.pending;
  }
}

const EVENT_ICON: Record<AgentEvent['type'], string> = {
  task_started: 'play_circle',
  task_completed: 'check_circle',
  task_failed: 'error',
  proposal_created: 'add_circle',
};

const EVENT_COLOR: Record<AgentEvent['type'], string> = {
  task_started: KC.info,
  task_completed: KC.done,
  task_failed: KC.error,
  proposal_created: KC.warning,
};

function GlassPanel({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div
      className="rounded overflow-hidden"
      style={{
        background: KC.surfaceContainer,
        border: `1px solid ${KC.surfaceBorder}`,
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        ...style,
      }}
    >
      {children}
    </div>
  );
}

function PanelHeader({ label }: { label: string }) {
  return (
    <div
      className="flex items-center px-4"
      style={{ height: 36, borderBottom: `1px solid ${KC.surfaceBorder}` }}
    >
      <span
        className="font-mono uppercase"
        style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}
      >
        {label}
      </span>
    </div>
  );
}

export function SessionDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: session, isLoading } = useSession(id);

  const chat = useScopedChat({
    scopeKind: 'session',
    entityId: id ?? '',
  });

  if (isLoading) {
    return (
      <div className="text-sm font-mono" style={{ color: KC.onSurfaceVariant }}>
        Loading session...
      </div>
    );
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
      {/* Back link */}
      <div style={{ marginBottom: 12 }}>
        <Link
          to="/sessions"
          className="font-mono"
          style={{ fontSize: 12, color: KC.onSurfaceVariant, textDecoration: 'none' }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLAnchorElement).style.color = KC.onSurface; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLAnchorElement).style.color = KC.onSurfaceVariant; }}
        >
          <span style={{ marginRight: 4 }}>←</span>Sessions
        </Link>
      </div>

      {/* Page header */}
      <div className="flex items-start justify-between" style={{ marginBottom: 16 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 18, fontWeight: 500, color: KC.onSurface, lineHeight: 1.2 }}>
            {session.taskType.replace(/_/g, ' ')}
          </h1>
          <span
            className="font-mono"
            style={{ fontSize: 12, color: KC.onSurfaceVariant }}
          >
            {session.agentCode} · started {formatRelativeTime(session.startedAt)}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span
            style={{
              display: 'inline-block',
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: statusDotColor(session.status),
            }}
          />
          <span
            className="font-mono"
            style={{ fontSize: 11, color: statusDotColor(session.status) }}
          >
            {session.status}
          </span>
          <StatusBadge status={session.status} className="ml-1" />
        </div>
      </div>

      {/* Meta row */}
      <div className="grid gap-3 mb-4" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
        {[
          { label: 'SESSION ID', value: session.id },
          { label: 'RUN ID', value: session.runId ?? '—' },
          { label: 'COMPLETED', value: session.completedAt ? formatRelativeTime(session.completedAt) : '—' },
        ].map(({ label, value }) => (
          <GlassPanel key={label}>
            <div style={{ padding: '10px 14px' }}>
              <div
                className="font-mono truncate"
                style={{ fontSize: 12, color: KC.onSurface, marginBottom: 2 }}
              >
                {value}
              </div>
              <div
                className="font-mono uppercase"
                style={{ fontSize: 10, color: KC.onSurfaceVariant, letterSpacing: '0.06em' }}
              >
                {label}
              </div>
            </div>
          </GlassPanel>
        ))}
      </div>

      {/* Timeline */}
      <GlassPanel style={{ marginBottom: 16 }}>
        <PanelHeader label="TIMELINE" />

        {session.events.length === 0 ? (
          <div style={{ padding: '24px 16px' }}>
            <EmptyState title="No events" description="No events have been recorded yet." />
          </div>
        ) : (
          <div style={{ padding: '8px 0' }}>
            {session.events.map((event, idx) => (
              <div
                key={event.id}
                className="flex items-start gap-3 px-4"
                style={{
                  paddingTop: 8,
                  paddingBottom: 8,
                  borderBottom:
                    idx < session.events.length - 1
                      ? `1px solid ${KC.surfaceBorderFaint}`
                      : 'none',
                }}
              >
                {/* Icon */}
                <span
                  className="material-symbols-outlined shrink-0"
                  style={{ fontSize: 15, color: EVENT_COLOR[event.type], lineHeight: 1.4 }}
                >
                  {EVENT_ICON[event.type]}
                </span>

                {/* Content */}
                <div className="flex-1 min-w-0">
                  <div style={{ fontSize: 13, color: KC.onSurface }}>
                    {event.message}
                  </div>
                  <div
                    className="font-mono"
                    style={{ fontSize: 11, color: KC.onSurfaceVariant, marginTop: 2 }}
                  >
                    {formatRelativeTime(event.timestamp)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </GlassPanel>

      {/* Session Chat Panel */}
      <div style={{ marginTop: 16 }}>
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
