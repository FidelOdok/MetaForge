import { beforeEach, describe, it, expect, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../../test/test-utils';
import type { HarnessRun } from '../../types/run';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useParams: () => ({ id: 'run_1' }) };
});

vi.mock('../../hooks/use-runs', () => ({
  useRun: vi.fn(),
}));

// The gate dialog reads the gate through the unified approvals API and decides
// through the shared ApprovalCard.
vi.mock('../../hooks/use-approvals', () => ({
  useApproval: vi.fn(),
  useDecideApproval: vi.fn(),
}));

// FORGE-395: the page reads the flow's display name from the gateway's
// catalogue instead of a hand-copied list in the dashboard, so the test has
// to supply one. The label here is the gateway's, not the old local string.
vi.mock('../../hooks/use-design-flows', () => ({
  useDesignFlows: () => ({
    data: {
      defaultFlowId: 'design_v1',
      flows: [
        {
          id: 'hardware_v1',
          name: 'Hardware & robotics lifecycle',
          label: 'Hardware & robotics',
          description: 'A multidisciplinary flow for a complete hardware system.',
          version: '1.0.0',
          isDefault: false,
          valid: true,
          violations: [],
          phases: [],
        },
      ],
    },
    isLoading: false,
    isError: false,
  }),
}));

import { RunDetailPage } from '../RunDetailPage';
import { useRun } from '../../hooks/use-runs';
import { useApproval, useDecideApproval } from '../../hooks/use-approvals';
import { makeApproval } from '../../test/approval-fixtures';

const mockUseRun = vi.mocked(useRun);
const mockUseApproval = vi.mocked(useApproval);
const mockUseDecide = vi.mocked(useDecideApproval);
const mutate = vi.fn();

const PAUSED: HarnessRun = {
  id: 'run_1',
  status: 'awaiting_approval',
  request: { kind: 'design_flow', flow: 'hardware_v1', goal: 'Build a quadruped', project_id: 'p1' },
  createdAt: 1_700_000_000,
  updatedAt: 1_700_000_100,
  approvalReason: 'Requirements sign-off',
  history: ['queued', 'running', 'awaiting_approval'],
};

function mockRun(value: Partial<ReturnType<typeof useRun>>) {
  mockUseRun.mockReturnValue({ refetch: vi.fn(), isFetching: false, ...value } as unknown as ReturnType<typeof useRun>);
}

beforeEach(() => {
  mutate.mockReset();
  mockUseDecide.mockReturnValue({
    mutate,
    reset: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
  } as unknown as ReturnType<typeof useDecideApproval>);
  mockUseApproval.mockReturnValue({
    data: makeApproval({ id: 'gate:run_1' }),
    isError: false,
  } as unknown as ReturnType<typeof useApproval>);
});

describe('RunDetailPage', () => {
  it('shows loading state', () => {
    mockRun({ data: undefined, isLoading: true });
    render(<RunDetailPage />);
    expect(screen.getByText('Loading run…')).toBeInTheDocument();
  });

  it('shows an error state when the gateway fails', () => {
    mockRun({ data: undefined, isLoading: false, isError: true });
    render(<RunDetailPage />);
    expect(screen.getByText('Run could not be loaded')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('shows not found', () => {
    mockRun({ data: undefined, isLoading: false });
    render(<RunDetailPage />);
    expect(screen.getByText('Run not found')).toBeInTheDocument();
  });

  it('renders the gate review, workflow name and history for a paused run', () => {
    mockRun({ data: PAUSED, isLoading: false });
    render(<RunDetailPage />);
    expect(screen.getByText('Build a quadruped')).toBeInTheDocument();
    expect(screen.getByText('Hardware & robotics')).toBeInTheDocument();
    expect(screen.getByText('Your review is required')).toBeInTheDocument();
    expect(screen.getByText('Requirements sign-off')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /inspect project artifacts/i })).toHaveAttribute('href', '/projects/p1');
    expect(screen.getByText('awaiting approval')).toBeInTheDocument();
    expect(screen.getByText('Latest recorded state')).toBeInTheDocument();
  });

  it('confirms an approval through the decision dialog', async () => {
    const user = userEvent.setup();
    mockRun({ data: PAUSED, isLoading: false });
    render(<RunDetailPage />);
    await user.click(screen.getByRole('button', { name: 'Review approval' }));
    expect(screen.getByText('Review this gate')).toBeInTheDocument();
    expect(mockUseApproval).toHaveBeenCalledWith('gate:run_1', true);
    await user.click(screen.getByRole('button', { name: /approve/i }));
    await user.click(screen.getByRole('button', { name: 'Confirm approve' }));
    expect(mutate).toHaveBeenCalledWith(
      { id: 'gate:run_1', body: { decision: 'approve' } },
      expect.any(Object),
    );
  });

  it('renders phase results from a completed run', () => {
    mockRun({
      data: {
        ...PAUSED,
        status: 'completed',
        approvalReason: undefined,
        result: {
          phases: [{ id: 'requirements', title: 'Requirements', status: 'ok', summary: 'Captured 6 requirements', artifacts: ['prd'] }],
        },
      },
      isLoading: false,
    });
    render(<RunDetailPage />);
    expect(screen.queryByText('Your review is required')).not.toBeInTheDocument();
    expect(screen.getByText('Captured 6 requirements')).toBeInTheDocument();
    expect(screen.getByText('Recorded artifacts')).toBeInTheDocument();
  });
});
