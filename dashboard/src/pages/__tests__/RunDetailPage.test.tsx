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
  useSubmitApproval: vi.fn(),
}));

import { RunDetailPage } from '../RunDetailPage';
import { useRun, useSubmitApproval } from '../../hooks/use-runs';

const mockUseRun = vi.mocked(useRun);
const mockUseSubmitApproval = vi.mocked(useSubmitApproval);
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
  mockUseSubmitApproval.mockReturnValue({
    mutate,
    reset: vi.fn(),
    isPending: false,
    isError: false,
  } as unknown as ReturnType<typeof useSubmitApproval>);
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
    expect(screen.getByText('Approve this gate?')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Approve & continue' }));
    expect(mutate).toHaveBeenCalledWith({ id: 'run_1', decision: 'approve' }, expect.any(Object));
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
