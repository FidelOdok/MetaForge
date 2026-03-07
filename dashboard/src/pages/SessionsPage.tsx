import { Link } from 'react-router-dom';
import { Card } from '../components/ui/Card';
import { StatusBadge } from '../components/shared/StatusBadge';
import { EmptyState } from '../components/ui/EmptyState';
import { formatRelativeTime } from '../utils/format-time';
import { useSessions } from '../hooks/use-sessions';

export function SessionsPage() {
  const { data: sessions, isLoading } = useSessions();

  if (isLoading) {
    return <div className="text-sm text-zinc-500">Loading sessions...</div>;
  }

  const items = sessions ?? [];

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">
          Agent Sessions
        </h2>
        <span className="text-sm text-zinc-500">{items.length} sessions</span>
      </div>

      {items.length === 0 ? (
        <EmptyState
          title="No sessions"
          description="Agent sessions will appear here when workflows run."
        />
      ) : (
        <div className="space-y-3">
          {items.map((session) => (
            <Link key={session.id} to={`/sessions/${session.id}`}>
              <Card className="flex items-center justify-between transition-shadow hover:shadow-md">
                <div className="flex items-center gap-4">
                  <div className="flex h-9 w-9 items-center justify-center rounded-full bg-zinc-100 text-xs font-bold text-zinc-700 dark:bg-zinc-700 dark:text-zinc-300">
                    {session.agentCode}
                  </div>
                  <div>
                    <div className="font-medium text-zinc-900 dark:text-zinc-100">
                      {session.taskType.replace(/_/g, ' ')}
                    </div>
                    <div className="text-xs text-zinc-400">
                      Started {formatRelativeTime(session.startedAt)}
                    </div>
                  </div>
                </div>
                <StatusBadge status={session.status} />
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
