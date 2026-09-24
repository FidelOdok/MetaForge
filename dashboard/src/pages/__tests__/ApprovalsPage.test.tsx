import { beforeEach, describe, it, expect, vi } from 'vitest';
import { render, screen } from '../../test/test-utils';

vi.mock('../../hooks/use-assistant', () => ({
  useProposals: vi.fn(),
  useDecideProposal: () => ({ mutate: vi.fn(), isPending: false }),
}));

// FORGE-33: real network call otherwise (getPendingToolApprovals hits
// /chat/tool_approvals) — same hermetic-mock treatment as use-assistant above.
// Defaults to an empty pending list so tests that don't care about this
// panel (most of them) don't need their own mockReturnValue.
vi.mock('../../hooks/use-tool-approvals', () => ({
  usePendingToolApprovals: vi.fn(() => ({ data: { runs: [] } })),
  useDecideToolApproval: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('../../hooks/use-runs', () => ({
  useRuns: vi.fn(),
}));

import { ApprovalsPage } from '../ApprovalsPage';
import { useRuns } from '../../hooks/use-runs';
import { useProposals } from '../../hooks/use-assistant';
import { usePendingToolApprovals } from '../../hooks/use-tool-approvals';

const mockUseProposals = vi.mocked(useProposals);
const mockUsePendingToolApprovals = vi.mocked(usePendingToolApprovals);
const mockUseRuns = vi.mocked(useRuns);

beforeEach(() => {
  mockUseRuns.mockReturnValue({ data: [], isLoading: false, isError: false } as unknown as ReturnType<typeof useRuns>);
});

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

  it('does not render static/fabricated gate content unrelated to real proposal data', () => {
    mockUseProposals.mockReturnValue({ data: { proposals: [], total: 0 }, isLoading: false } as unknown as ReturnType<typeof useProposals>);
    render(<ApprovalsPage />);
    // These used to be hardcoded regardless of the real (zero) counts above them.
    expect(screen.queryByText('AT-RISK')).not.toBeInTheDocument();
    expect(screen.queryByText('READY')).not.toBeInTheDocument();
    expect(screen.queryByText('IN PROGRESS')).not.toBeInTheDocument();
    expect(screen.queryByText('W3 Gate Check')).not.toBeInTheDocument();
    expect(screen.queryByText('CHECKLIST')).not.toBeInTheDocument();
    expect(screen.queryByText('Design review sign-off')).not.toBeInTheDocument();
    expect(screen.queryByText('P9')).not.toBeInTheDocument();
  });

  it('FORGE-33: shows empty state for pending tool calls by default', () => {
    mockUseProposals.mockReturnValue({ data: { proposals: [], total: 0 }, isLoading: false } as unknown as ReturnType<typeof useProposals>);
    render(<ApprovalsPage />);
    expect(screen.getByText('No tool calls awaiting approval')).toBeInTheDocument();
  });

  it('FORGE-33: renders a pending tool call awaiting approval', () => {
    mockUseProposals.mockReturnValue({ data: { proposals: [], total: 0 }, isLoading: false } as unknown as ReturnType<typeof useProposals>);
    mockUsePendingToolApprovals.mockReturnValue({
      data: {
        runs: [{
          id: 'run_1',
          status: 'awaiting_approval',
          request: { tool: 'mcp_twin_commit_geometry', arguments: { name: 'leg bracket' } },
          created_at: Date.now() / 1000,
          updated_at: Date.now() / 1000,
          error: null,
          approval_reason: "approval required for tool 'mcp_twin_commit_geometry'",
          result: null,
          history: ['queued', 'running', 'awaiting_approval'],
        }],
      },
    } as unknown as ReturnType<typeof usePendingToolApprovals>);
    render(<ApprovalsPage />);
    expect(screen.getByText('mcp_twin_commit_geometry')).toBeInTheDocument();
    expect(screen.getByText(/leg bracket/)).toBeInTheDocument();
    expect(screen.queryByText('No tool calls awaiting approval')).not.toBeInTheDocument();
  });

  it('renders proposals', () => {
    mockUsePendingToolApprovals.mockReturnValue({ data: { runs: [] } } as unknown as ReturnType<typeof usePendingToolApprovals>);
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

  it('shows an error state when proposals cannot be loaded', () => {
    mockUseProposals.mockReturnValue({ data: undefined, isLoading: false, isError: true } as unknown as ReturnType<typeof useProposals>);
    render(<ApprovalsPage />);
    expect(screen.getByText('Proposals could not be loaded')).toBeInTheDocument();
    expect(screen.getByText('Run approval gates')).toBeInTheDocument();
  });

  it('lists runs paused at an approval gate, linking to the run', () => {
    mockUseProposals.mockReturnValue({ data: { proposals: [], total: 0 }, isLoading: false } as unknown as ReturnType<typeof useProposals>);
    mockUseRuns.mockReturnValue({
      data: [
        { id: 'run_gate', status: 'awaiting_approval', request: { goal: 'lift 2 kg' }, createdAt: 0, updatedAt: 0, approvalReason: 'Requirements sign-off', history: [] },
        { id: 'run_done', status: 'completed', request: { goal: 'finished run' }, createdAt: 0, updatedAt: 0, history: [] },
      ],
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useRuns>);
    render(<ApprovalsPage />);
    expect(screen.getByText('lift 2 kg').closest('a')).toHaveAttribute('href', '/runs/run_gate');
    expect(screen.getByText('Requirements sign-off')).toBeInTheDocument();
    expect(screen.queryByText('finished run')).not.toBeInTheDocument();
  });

  it('reports when run gates cannot be loaded', () => {
    mockUseProposals.mockReturnValue({ data: { proposals: [], total: 0 }, isLoading: false } as unknown as ReturnType<typeof useProposals>);
    mockUseRuns.mockReturnValue({ data: undefined, isLoading: false, isError: true } as unknown as ReturnType<typeof useRuns>);
    render(<ApprovalsPage />);
    expect(screen.getByText(/Run gates could not be loaded/)).toBeInTheDocument();
  });
});
