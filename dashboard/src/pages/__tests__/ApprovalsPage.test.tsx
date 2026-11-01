import { beforeEach, describe, it, expect, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../../test/test-utils';

vi.mock('../../hooks/use-approvals', () => ({
  useApprovals: vi.fn(),
  useDecideApproval: () => ({ mutate: vi.fn(), isPending: false, isError: false, reset: vi.fn(), error: null }),
}));

import { ApprovalsPage } from '../ApprovalsPage';
import { useApprovals } from '../../hooks/use-approvals';
import { makeApproval } from '../../test/approval-fixtures';

const mockUseApprovals = vi.mocked(useApprovals);

function mockList(value: Record<string, unknown>) {
  mockUseApprovals.mockReturnValue(value as unknown as ReturnType<typeof useApprovals>);
}

beforeEach(() => {
  mockUseApprovals.mockReset();
  mockList({ data: { items: [], unscopedCount: 0 }, isLoading: false, isError: false });
});

describe('ApprovalsPage', () => {
  it('shows loading state', () => {
    mockList({ data: undefined, isLoading: true, isError: false });
    const { container } = render(<ApprovalsPage />);
    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0);
  });

  it('shows empty state', () => {
    render(<ApprovalsPage />);
    expect(screen.getByText('Nothing awaiting approval')).toBeInTheDocument();
  });

  it('shows an error state', () => {
    mockList({ data: undefined, isLoading: false, isError: true });
    render(<ApprovalsPage />);
    expect(screen.getByText('Approvals could not be loaded')).toBeInTheDocument();
  });

  it('renders every kind through one card', () => {
    mockList({
      data: {
        items: [
          makeApproval(),
          makeApproval({ id: 'tool:run_d', kind: 'tool_call', title: 'Held write', allowed_decisions: ['approve', 'reject'], detail: { tool: 'project.delete', arguments: {} } }),
          makeApproval({ id: 'change:1', kind: 'design_change', title: 'Widen rib', detail: { diff: {}, affected: [] } }),
        ],
        unscopedCount: 0,
      },
      isLoading: false,
      isError: false,
    });
    render(<ApprovalsPage />);
    expect(screen.getAllByTestId('approval-card')).toHaveLength(3);
    expect(screen.getByText('Held write')).toBeInTheDocument();
    expect(screen.getByText('Widen rib')).toBeInTheDocument();
  });

  it('reports items hidden for having no project', () => {
    mockList({ data: { items: [], unscopedCount: 2 }, isLoading: false, isError: false });
    render(<ApprovalsPage />);
    expect(screen.getByTestId('approvals-unscoped')).toHaveTextContent('2 items not shown');
  });

  it('queries decided items on the Audit tab and shows who, surface and verification', async () => {
    const user = userEvent.setup();
    mockUseApprovals.mockImplementation(((f: { status?: string }) =>
      f.status === 'decided'
        ? {
            data: {
              items: [
                makeApproval({
                  status: 'approved',
                  decision: {
                    decision: 'approve',
                    reason: 'looks good',
                    approver: 'ada',
                    approver_verified: true,
                    surface: 'agent',
                    on_behalf_of: 'grace',
                    agent: 'claude-code',
                    decided_at: '2026-10-03T11:00:00Z',
                  },
                }),
              ],
              unscopedCount: 0,
            },
            isLoading: false,
            isError: false,
          }
        : { data: { items: [], unscopedCount: 0 }, isLoading: false, isError: false }) as unknown as typeof useApprovals);
    render(<ApprovalsPage />);
    await user.click(screen.getByRole('tab', { name: 'AUDIT' }));
    expect(mockUseApprovals).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'decided' }));
    const row = screen.getByTestId('audit-row');
    expect(row).toHaveTextContent('ada');
    expect(row).toHaveTextContent('agent');
    expect(row).toHaveTextContent('grace');
    expect(row).toHaveTextContent('claude-code');
    expect(row).toHaveTextContent('verified');
    expect(row).toHaveTextContent('approve: looks good');
  });

  it('passes the kind filter to the query', async () => {
    const user = userEvent.setup();
    render(<ApprovalsPage />);
    await user.selectOptions(screen.getByLabelText('Filter by kind'), 'tool_call');
    expect(mockUseApprovals).toHaveBeenLastCalledWith(expect.objectContaining({ kind: 'tool_call' }));
  });
});
