import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '../../test/test-utils';

vi.mock('../../hooks/use-sessions', () => ({
  useSessions: vi.fn(),
}));

// The pending-approval card on this page reads proposals. Mocked so the
// scoping assertions below are about what the page asks for, not about a
// real request going out.
vi.mock('../../hooks/use-assistant', () => ({
  useProposals: vi.fn(() => ({ data: { proposals: [], total: 0 } })),
  useDecideProposal: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('../../hooks/use-active-project', () => ({
  useActiveProject: vi.fn(),
}));

import { SessionsPage } from '../SessionsPage';
import { useSessions } from '../../hooks/use-sessions';
import { useProposals } from '../../hooks/use-assistant';
import { useActiveProject } from '../../hooks/use-active-project';

const mockUseSessions = vi.mocked(useSessions);
const mockUseProposals = vi.mocked(useProposals);
const mockActiveProject = vi.mocked(useActiveProject);

const PROJECT = '11111111-1111-1111-1111-111111111111';

beforeEach(() => {
  mockActiveProject.mockReturnValue({ activeProjectId: null } as unknown as ReturnType<
    typeof useActiveProject
  >);
  mockUseProposals.mockReturnValue({
    data: { proposals: [], total: 0 },
  } as unknown as ReturnType<typeof useProposals>);
});

const COMPLETED_SESSION = {
  id: 's1',
  agentCode: 'MECH',
  taskType: 'validate_stress',
  status: 'completed' as const,
  startedAt: new Date(Date.now() - 10000).toISOString(),
  completedAt: new Date().toISOString(),
  events: [],
};

describe('SessionsPage', () => {
  it('shows loading state', () => {
    mockUseSessions.mockReturnValue({ data: undefined, isLoading: true } as unknown as ReturnType<typeof useSessions>);
    render(<SessionsPage />);
    expect(screen.getByText('Loading…')).toBeInTheDocument();
  });

  it('shows empty state', () => {
    mockUseSessions.mockReturnValue({ data: { sessions: [], unscopedCount: 0 }, isLoading: false } as unknown as ReturnType<typeof useSessions>);
    render(<SessionsPage />);
    expect(screen.getByText('Orchestrator')).toBeInTheDocument();
  });

  it('shows honest empty states instead of fake demo content when there are no sessions', () => {
    mockUseSessions.mockReturnValue({ data: { sessions: [], unscopedCount: 0 }, isLoading: false } as unknown as ReturnType<typeof useSessions>);
    render(<SessionsPage />);
    // The DAG and execution log panels used to fall back to a fabricated
    // "schematic RUNNING" workflow and fixed timestamps that contradicted
    // the real "0 workflows running" state — they must not appear.
    expect(screen.queryByText('schematic')).not.toBeInTheDocument();
    expect(screen.queryByText(/12:04/)).not.toBeInTheDocument();
    expect(screen.getByText('No workflow runs yet')).toBeInTheDocument();
    expect(screen.getByText('No execution log yet')).toBeInTheDocument();
  });

  it('renders session list', () => {
    mockUseSessions.mockReturnValue({
      data: { sessions: [COMPLETED_SESSION], unscopedCount: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof useSessions>);
    render(<SessionsPage />);
    expect(screen.getByText('validate stress')).toBeInTheDocument();
    expect(within(screen.getByTestId('session-row')).getByText('MECH')).toBeInTheDocument();
  });

  it('shows status text for completed session', () => {
    mockUseSessions.mockReturnValue({
      data: { sessions: [COMPLETED_SESSION], unscopedCount: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof useSessions>);
    render(<SessionsPage />);
    expect(within(screen.getByTestId('session-row')).getByText('completed')).toBeInTheDocument();
  });

  it('shows status text for failed session', () => {
    mockUseSessions.mockReturnValue({
      data: { sessions: [{ ...COMPLETED_SESSION, status: 'failed' as const }], unscopedCount: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof useSessions>);
    render(<SessionsPage />);
    expect(within(screen.getByTestId('session-row')).getByText('failed')).toBeInTheDocument();
  });

  it('shows status text for running session', () => {
    mockUseSessions.mockReturnValue({
      data: { sessions: [{ ...COMPLETED_SESSION, status: 'running' as const, completedAt: undefined }], unscopedCount: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof useSessions>);
    render(<SessionsPage />);
    expect(within(screen.getByTestId('session-row')).getByText('running')).toBeInTheDocument();
  });

  it('does not warn about duplicate React keys when multiple sessions share a taskType', () => {
    // Regression: the DAG panel keyed each node by its (non-unique) task
    // label ("chat"), so any two queued sessions of the same type -- the
    // common case, e.g. several queued "chat" tasks -- triggered React's
    // "Encountered two children with the same key" warning live on
    // /sessions. Key by the session's actual unique id instead.
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const sameTypeSessions = Array.from({ length: 5 }, (_, i) => ({
      ...COMPLETED_SESSION,
      id: `session-${i}`,
      taskType: 'chat',
      status: 'pending' as const,
      completedAt: undefined,
    }));
    mockUseSessions.mockReturnValue({
      data: sameTypeSessions,
      isLoading: false,
    } as unknown as ReturnType<typeof useSessions>);

    render(<SessionsPage />);

    const duplicateKeyWarning = consoleError.mock.calls.some((args) =>
      String(args[0]).includes('Encountered two children with the same key')
    );
    expect(duplicateKeyWarning).toBe(false);
    consoleError.mockRestore();
  });
});

describe('SessionsPage project scoping', () => {
  it('asks only for the active project\'s pending proposals', () => {
    // The gap. This page called `useProposals()` bare while the Approvals
    // page passed the project to the same hook — so reading one project's
    // sessions could surface, and let you approve, a proposal belonging to
    // another, with nothing on the card naming which.
    mockActiveProject.mockReturnValue({ activeProjectId: PROJECT } as unknown as ReturnType<
      typeof useActiveProject
    >);
    mockUseSessions.mockReturnValue({
      data: { sessions: [], unscopedCount: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof useSessions>);
    render(<SessionsPage />);
    expect(mockUseProposals).toHaveBeenCalledWith(PROJECT);
    expect(mockUseProposals).not.toHaveBeenCalledWith();
  });

  it('asks for the sessions of that project too', () => {
    mockActiveProject.mockReturnValue({ activeProjectId: PROJECT } as unknown as ReturnType<
      typeof useActiveProject
    >);
    mockUseSessions.mockReturnValue({
      data: { sessions: [], unscopedCount: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof useSessions>);
    render(<SessionsPage />);
    expect(mockUseSessions).toHaveBeenCalledWith(PROJECT);
  });

  it('says how many internal workflow runs were hidden', () => {
    // Internal Temporal runs carry no project and drop out once one is
    // selected. Without this the page just gets shorter, which reads as
    // "nothing ran here".
    mockActiveProject.mockReturnValue({ activeProjectId: PROJECT } as unknown as ReturnType<
      typeof useActiveProject
    >);
    mockUseSessions.mockReturnValue({
      data: { sessions: [], unscopedCount: 4 },
      isLoading: false,
    } as unknown as ReturnType<typeof useSessions>);
    render(<SessionsPage />);
    const note = screen.getByTestId('sessions-unscoped');
    expect(note).toHaveTextContent('4 internal workflow runs not shown');
    expect(note).toHaveTextContent('no project recorded');
  });

  it('says nothing when nothing is hidden', () => {
    mockUseSessions.mockReturnValue({
      data: { sessions: [], unscopedCount: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof useSessions>);
    render(<SessionsPage />);
    expect(screen.queryByTestId('sessions-unscoped')).not.toBeInTheDocument();
  });
});

describe('Agent roster', () => {
  // It was a hardcoded array of four rows -- "Requirements Agent: running
  // spec" with a pulsing dot, "Mechanical Agent: idle" -- rendered
  // identically forever, on every project, whatever was happening. Those
  // four also do not exist as running things: `domain_agents/` holds the
  // disciplines as code invoked during a run, and nothing keeps a
  // per-discipline process with a status. The real record is which agent ran
  // a session: chat-harness, mcp, claude-code and friends.
  const session = (
    agentCode: string,
    status: string,
    startedAt: string,
    completedAt?: string,
  ) => ({
    id: `${agentCode}-${startedAt}`,
    agentCode,
    taskType: 'task',
    status: status as 'completed',
    startedAt,
    completedAt,
    events: [],
  });

  function renderWith(sessions: ReturnType<typeof session>[]) {
    mockUseSessions.mockReturnValue({
      data: { sessions, unscopedCount: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof useSessions>);
    render(<SessionsPage />);
  }

  it('lists the agents that actually ran, not four invented ones', () => {
    renderWith([
      session('chat-harness', 'completed', '2026-10-01T10:00:00Z', '2026-10-01T10:05:00Z'),
      session('mcp', 'running', '2026-10-02T10:00:00Z'),
    ]);
    const roster = screen.getAllByTestId('agent-roster-row');
    const text = roster.map((r) => r.textContent).join(' ');
    expect(text).toContain('chat-harness');
    expect(text).toContain('mcp');
    expect(screen.queryByText('Requirements Agent')).not.toBeInTheDocument();
    expect(screen.queryByText('Mechanical Agent')).not.toBeInTheDocument();
  });

  it('shows one row per agent, however many sessions it ran', () => {
    renderWith([
      session('mcp', 'completed', '2026-10-01T10:00:00Z', '2026-10-01T10:01:00Z'),
      session('mcp', 'completed', '2026-10-02T10:00:00Z', '2026-10-02T10:01:00Z'),
      session('claude-code', 'completed', '2026-10-01T09:00:00Z', '2026-10-01T09:01:00Z'),
    ]);
    expect(screen.getAllByTestId('agent-roster-row')).toHaveLength(2);
  });

  it('reports the most recent session, not whichever came first', () => {
    // Deliberately out of order: a panel that trusts its caller's sort is one
    // re-sort away from showing a stale status as current.
    renderWith([
      session('mcp', 'failed', '2026-10-01T10:00:00Z', '2026-10-01T10:01:00Z'),
      session('mcp', 'running', '2026-10-03T10:00:00Z'),
      session('mcp', 'completed', '2026-10-02T10:00:00Z', '2026-10-02T10:01:00Z'),
    ]);
    const row = screen.getByTestId('agent-roster-row');
    expect(row).toHaveTextContent('running');
    expect(row).not.toHaveTextContent('failed');
  });

  it('says so when no agent has run here', () => {
    // An empty roster is a real answer, and a truer one than four invented
    // rows on a project nothing has touched.
    renderWith([]);
    expect(screen.getByTestId('agent-roster-empty')).toHaveTextContent(
      'No agent has run a session in this project yet',
    );
    expect(screen.queryByTestId('agent-roster-row')).not.toBeInTheDocument();
  });
});
