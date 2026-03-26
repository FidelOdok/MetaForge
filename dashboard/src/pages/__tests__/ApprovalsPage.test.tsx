import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '../../test/test-utils';

vi.mock('../../hooks/use-assistant', () => ({
  useProposals: vi.fn(),
  useDecideProposal: () => ({ mutate: vi.fn(), isPending: false }),
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

import { ApprovalsPage } from '../ApprovalsPage';
import { useProposals } from '../../hooks/use-assistant';

const mockUseProposals = vi.mocked(useProposals);

describe('ApprovalsPage', () => {
  it('shows loading state', () => {
    mockUseProposals.mockReturnValue({ data: undefined, isLoading: true } as unknown as ReturnType<typeof useProposals>);
    const { container } = render(<ApprovalsPage />);
    // KC renders animate-pulse skeleton divs (no data-testid)
    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0);
  });

  it('shows empty state', () => {
    mockUseProposals.mockReturnValue({ data: { proposals: [], total: 0 }, isLoading: false } as unknown as ReturnType<typeof useProposals>);
    render(<ApprovalsPage />);
    expect(screen.getByText('No pending proposals')).toBeInTheDocument();
  });

  it('renders proposals', () => {
    mockUseProposals.mockReturnValue({
      data: {
        proposals: [{
          change_id: 'c1',
          agent_code: 'MECH',
          description: 'Update stress report',
          diff: {},
          work_products_affected: [],
          status: 'pending',
          session_id: 's1',
          created_at: new Date().toISOString(),
          decided_at: null,
          decision_reason: null,
          reviewer: null,
        }],
        total: 1,
      },
      isLoading: false,
    } as unknown as ReturnType<typeof useProposals>);
    render(<ApprovalsPage />);
    expect(screen.getByText('Update stress report')).toBeInTheDocument();
  });
});
