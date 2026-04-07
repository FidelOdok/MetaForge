import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '../../test/test-utils';

vi.mock('../../hooks/use-sessions', () => ({
  useSessions: vi.fn(),
}));

import { SessionsPage } from '../SessionsPage';
import { useSessions } from '../../hooks/use-sessions';

const mockUseSessions = vi.mocked(useSessions);

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
    mockUseSessions.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useSessions>);
    render(<SessionsPage />);
    // New orchestrator layout shows static DAG + log when no sessions
    expect(screen.getByText('Orchestrator')).toBeInTheDocument();
  });

  it('renders session list', () => {
    mockUseSessions.mockReturnValue({
      data: [COMPLETED_SESSION],
      isLoading: false,
    } as unknown as ReturnType<typeof useSessions>);
    render(<SessionsPage />);
    expect(screen.getByText('validate stress')).toBeInTheDocument();
    expect(screen.getByText('MECH')).toBeInTheDocument();
  });

  it('shows status text for completed session', () => {
    mockUseSessions.mockReturnValue({
      data: [COMPLETED_SESSION],
      isLoading: false,
    } as unknown as ReturnType<typeof useSessions>);
    render(<SessionsPage />);
    expect(screen.getByText('completed')).toBeInTheDocument();
  });

  it('shows status text for failed session', () => {
    mockUseSessions.mockReturnValue({
      data: [{ ...COMPLETED_SESSION, status: 'failed' as const }],
      isLoading: false,
    } as unknown as ReturnType<typeof useSessions>);
    render(<SessionsPage />);
    expect(screen.getByText('failed')).toBeInTheDocument();
  });

  it('shows status text for running session', () => {
    mockUseSessions.mockReturnValue({
      data: [{ ...COMPLETED_SESSION, status: 'running' as const, completedAt: undefined }],
      isLoading: false,
    } as unknown as ReturnType<typeof useSessions>);
    render(<SessionsPage />);
    expect(screen.getByText('running')).toBeInTheDocument();
  });
});
