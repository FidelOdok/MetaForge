import { Link, useParams } from 'react-router-dom';
import { Card } from '../components/ui/Card';
import { StatusBadge } from '../components/shared/StatusBadge';
import { formatRelativeTime } from '../utils/format-time';
import { EmptyState } from '../components/ui/EmptyState';
import type { AgentSession, AgentEvent } from '../types/session';

/** Mock session data until real SSE/polling is wired. */
const MOCK_SESSIONS: Record<string, AgentSession> = {
  'sess-001': {
    id: 'sess-001',
    agentCode: 'MECH',
    taskType: 'validate_stress',
    status: 'completed',
    startedAt: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
    completedAt: new Date(Date.now() - 28 * 60 * 1000).toISOString(),
    runId: 'run-001',
    events: [
      {
        id: 'evt-1',
        timestamp: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
        type: 'task_started',
        agentCode: 'MECH',
        message: 'Started stress validation on bracket assembly',
      },
      {
        id: 'evt-2',
        timestamp: new Date(Date.now() - 29 * 60 * 1000).toISOString(),
        type: 'proposal_created',
        agentCode: 'MECH',
        message: 'Proposed stress report for review',
      },
      {
        id: 'evt-3',
        timestamp: new Date(Date.now() - 28 * 60 * 1000).toISOString(),
        type: 'task_completed',
        agentCode: 'MECH',
        message: 'Stress validation completed — all constraints met',
      },
    ],
  },
  'sess-002': {
    id: 'sess-002',
    agentCode: 'EE',
    taskType: 'run_erc',
    status: 'running',
    startedAt: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
    runId: 'run-002',
    events: [
      {
        id: 'evt-4',
        timestamp: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
        type: 'task_started',
        agentCode: 'EE',
        message: 'Started ERC check on power supply schematic',
      },
    ],
  },
  'sess-003': {
    id: 'sess-003',
    agentCode: 'MECH',
    taskType: 'generate_mesh',
    status: 'failed',
    startedAt: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
    completedAt: new Date(Date.now() - 58 * 60 * 1000).toISOString(),
    events: [
      {
        id: 'evt-5',
        timestamp: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
        type: 'task_started',
        agentCode: 'MECH',
        message: 'Started mesh generation for housing',
      },
      {
        id: 'evt-6',
        timestamp: new Date(Date.now() - 58 * 60 * 1000).toISOString(),
        type: 'task_failed',
        agentCode: 'MECH',
        message: 'Mesh generation failed — geometry too complex for target element size',
      },
    ],
  },
};

const EVENT_ICONS: Record<AgentEvent['type'], string> = {
  task_started: '▶',
  task_completed: '✓',
  task_failed: '✗',
  proposal_created: '◆',
};

const EVENT_COLORS: Record<AgentEvent['type'], string> = {
  task_started: 'text-blue-500',
  task_completed: 'text-green-500',
  task_failed: 'text-red-500',
  proposal_created: 'text-amber-500',
};

export function SessionDetailPage() {
  const { id } = useParams<{ id: string }>();
  const session = id ? MOCK_SESSIONS[id] : undefined;

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
            {session.runId ?? '—'}
          </div>
          <div className="text-xs text-zinc-500">Run ID</div>
        </Card>
        <Card>
          <div className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
            {session.completedAt ? formatRelativeTime(session.completedAt) : '—'}
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
    </div>
  );
}
