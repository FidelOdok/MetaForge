import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '../../../test/test-utils';
import { NodeProposals } from '../NodeProposals';
import type { Proposal } from '../../../api/endpoints/assistant';

const mockMutate = vi.fn();
let mockProposals: Proposal[] = [];

vi.mock('../../../hooks/use-assistant', () => ({
  assistantKeys: { all: ['assistant'] },
  useProposals: vi.fn(() => ({ data: { proposals: mockProposals, total: mockProposals.length } })),
  useDecideProposal: vi.fn(() => ({ mutate: mockMutate, isPending: false })),
}));

function makeProposal(overrides: Partial<Proposal> = {}): Proposal {
  return {
    change_id: 'chg-1',
    agent_code: 'mechanical',
    description: 'Regenerate base plate with 4mm fillet',
    diff: { action: 'record_decision' },
    work_products_affected: ['node-001'],
    status: 'pending',
    session_id: 'sess-1',
    project_id: null,
    created_at: '2026-01-01T00:00:00Z',
    decided_at: null,
    decision_reason: null,
    reviewer: null,
    ...overrides,
  };
}

describe('NodeProposals', () => {
  beforeEach(() => {
    mockMutate.mockClear();
    mockProposals = [];
  });

  it('renders nothing when there are no matching pending proposals', () => {
    const { container } = render(<NodeProposals nodeId="node-001" />);
    expect(container.firstChild).toBeNull();
  });

  it('shows a pending proposal that affects the node', () => {
    mockProposals = [makeProposal()];
    render(<NodeProposals nodeId="node-001" />);
    expect(screen.getByText('Regenerate base plate with 4mm fillet')).toBeInTheDocument();
    expect(screen.getByText('record_decision')).toBeInTheDocument();
  });

  it('filters out proposals that do not affect the node', () => {
    mockProposals = [makeProposal({ work_products_affected: ['other-node'] })];
    const { container } = render(<NodeProposals nodeId="node-001" />);
    expect(container.firstChild).toBeNull();
  });

  it('approving calls decide with approve', () => {
    mockProposals = [makeProposal()];
    render(<NodeProposals nodeId="node-001" />);
    fireEvent.click(screen.getByText('Approve'));
    expect(mockMutate).toHaveBeenCalledWith(
      expect.objectContaining({ changeId: 'chg-1', decision: 'approve' }),
      expect.anything(),
    );
  });

  it('rejecting calls decide with reject', () => {
    mockProposals = [makeProposal()];
    render(<NodeProposals nodeId="node-001" />);
    fireEvent.click(screen.getByText('Reject'));
    expect(mockMutate).toHaveBeenCalledWith(
      expect.objectContaining({ changeId: 'chg-1', decision: 'reject' }),
      expect.anything(),
    );
  });
});
