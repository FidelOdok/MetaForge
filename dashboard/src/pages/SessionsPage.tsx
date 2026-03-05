import { Link } from 'react-router-dom';
import { Card } from '../components/ui/Card';
import { StatusBadge } from '../components/shared/StatusBadge';
import { formatRelativeTime } from '../utils/format-time';
import type { AgentSession } from '../types/session';

/** Mock session data until real SSE/polling is wired. */
const MOCK_SESSIONS: AgentSession[] = [
  {
    id: 'sess-001',
    agentCode: 'MECH',
    taskType: 'validate_stress',
    status: 'completed',
    startedAt: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
    completedAt: new Date(Date.now() - 28 * 60 * 1000).toISOString(),
    events: [],
    runId: 'run-001',
  },
  {
    id: 'sess-002',
    agentCode: 'EE',
    taskType: 'run_erc',
    status: 'running',
    startedAt: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
    events: [],
    runId: 'run-002',
  },
  {
    id: 'sess-003',
    agentCode: 'MECH',
    taskType: 'generate_mesh',
    status: 'failed',
    startedAt: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
    completedAt: new Date(Date.now() - 58 * 60 * 1000).toISOString(),
    events: [],
  },
];

export function SessionsPage() {
  const sessions = MOCK_SESSIONS;

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">
          Agent Sessions
        </h2>
        <span className="text-sm text-zinc-500">{sessions.length} sessions</span>
      </div>

      <div className="space-y-3">
        {sessions.map((session) => (
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
    </div>
  );
}
