import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '../../test/test-utils';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useParams: () => ({ id: 'sess-001' }) };
});

vi.mock('../../hooks/use-sessions', () => ({
  useSession: vi.fn(),
}));

vi.mock('../../hooks/use-scoped-chat', () => ({
  useScopedChat: () => ({
    thread: null,
    messages: [],
    isTyping: false,
    sendMessage: vi.fn(),
    createThread: vi.fn(),
    isLoading: false,
    isCreating: false,
    isSending: false,
  }),
}));

import { SessionDetailPage } from '../SessionDetailPage';
import { useSession } from '../../hooks/use-sessions';

const mockUseSession = vi.mocked(useSession);

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
      data: {
        id: 'sess-001',
        agentCode: 'MECH',
        taskType: 'validate_stress',
        status: 'completed',
        startedAt: new Date().toISOString(),
        completedAt: new Date().toISOString(),
        runId: 'run-001',
        events: [
          { id: 'e1', timestamp: new Date().toISOString(), type: 'task_started', agentCode: 'MECH', message: 'Started stress validation' },
        ],
      },
      isLoading: false,
    } as unknown as ReturnType<typeof useSession>);
    render(<SessionDetailPage />);
    expect(screen.getByText('validate stress')).toBeInTheDocument();
    expect(screen.getByText('Started stress validation')).toBeInTheDocument();
  });
});
