import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '../../test/test-utils';

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
    expect(screen.getByText('MECH')).toBeInTheDocument();
  });

  it('shows status text for completed session', () => {
    mockUseSessions.mockReturnValue({
      data: { sessions: [COMPLETED_SESSION], unscopedCount: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof useSessions>);
    render(<SessionsPage />);
    expect(screen.getByText('completed')).toBeInTheDocument();
  });

  it('shows status text for failed session', () => {
    mockUseSessions.mockReturnValue({
      data: { sessions: [{ ...COMPLETED_SESSION, status: 'failed' as const }], unscopedCount: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof useSessions>);
    render(<SessionsPage />);
    expect(screen.getByText('failed')).toBeInTheDocument();
  });

  it('shows status text for running session', () => {
    mockUseSessions.mockReturnValue({
      data: { sessions: [{ ...COMPLETED_SESSION, status: 'running' as const, completedAt: undefined }], unscopedCount: 0 },
      isLoading: false,
    } as unknown as ReturnType<typeof useSessions>);
    render(<SessionsPage />);
    expect(screen.getByText('running')).toBeInTheDocument();
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
