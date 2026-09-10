import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '../../test/test-utils';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useParams: () => ({ id: 'sess-001' }) };
});

vi.mock('../../hooks/use-sessions', () => ({
  useSession: vi.fn(),
}));

import { SessionDetailPage } from '../SessionDetailPage';
import { useSession } from '../../hooks/use-sessions';

const mockUseSession = vi.mocked(useSession);

const SESSION_WITH_EVENTS = {
  id: 'sess-001',
  agentCode: 'MECH',
  taskType: 'validate_stress',
  status: 'completed',
  startedAt: new Date(Date.now() - 5000).toISOString(),
  completedAt: new Date().toISOString(),
  runId: 'run-001',
  events: [
    {
      id: 'e1',
      timestamp: new Date(Date.now() - 5000).toISOString(),
      type: 'task_started',
      agentCode: 'MECH',
      message: 'Started stress validation',
    },
    {
      id: 'e2',
      timestamp: new Date().toISOString(),
      type: 'task_completed',
      agentCode: 'MECH',
      message: 'Completed stress validation',
    },
  ],
};

describe('SessionDetailPage', () => {
  it('shows loading state', () => {
    mockUseSession.mockReturnValue({ data: undefined, isLoading: true } as ReturnType<typeof useSession>);
    render(<SessionDetailPage />);
    expect(screen.getByText('Loading session...')).toBeInTheDocument();
  });

  it('shows not found', () => {
    mockUseSession.mockReturnValue({ data: undefined, isLoading: false } as ReturnType<typeof useSession>);
    render(<SessionDetailPage />);
    expect(screen.getByText('Session not found')).toBeInTheDocument();
  });

  it('renders session detail with events', () => {
    mockUseSession.mockReturnValue({
      data: SESSION_WITH_EVENTS,
      isLoading: false,
    } as unknown as ReturnType<typeof useSession>);
    render(<SessionDetailPage />);
    expect(screen.getByText('validate stress')).toBeInTheDocument();
    expect(screen.getByText('Started stress validation')).toBeInTheDocument();
  });

  it('renders the session status exactly once, not duplicated', () => {
    // Regression (MET-675): the header rendered both an inline dot+text
    // status label (verbatim lowercase, e.g. "completed") AND a StatusBadge
    // ("Completed") right next to it -- the same status shown twice in two
    // different visual styles. Matched exact-case so this doesn't collide
    // with the unrelated "COMPLETED" meta-row field label.
    mockUseSession.mockReturnValue({
      data: SESSION_WITH_EVENTS,
      isLoading: false,
    } as unknown as ReturnType<typeof useSession>);
    render(<SessionDetailPage />);
    expect(screen.queryByText('completed')).not.toBeInTheDocument();
    expect(screen.getByText('Completed')).toBeInTheDocument();
  });

  it('renders an icon for real MCP-capture event types, not just the legacy workflow-run ones', () => {
    // Regression (MET-675): AgentEvent['type'] was a closed union of legacy
    // workflow-run values (task_started/task_completed/task_failed/
    // proposal_created), but real captured sessions use the MCP capture
    // vocabulary (thought/action/decision/observation/error/result) --
    // EVENT_ICON/EVENT_COLOR lookups returned undefined for every real
    // event, silently rendering a blank icon for effectively every session
    // in the system.
    mockUseSession.mockReturnValue({
      data: {
        ...SESSION_WITH_EVENTS,
        events: [
          {
            id: 'e3',
            timestamp: new Date().toISOString(),
            type: 'action',
            agentCode: 'MECH',
            message: 'twin.find_by_property failed',
          },
        ],
      },
      isLoading: false,
    } as unknown as ReturnType<typeof useSession>);
    render(<SessionDetailPage />);
    const icon = document.querySelector('.material-symbols-outlined');
    expect(icon?.textContent).toBeTruthy();
  });
});
