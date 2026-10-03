import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../../../test/test-utils';

vi.mock('../../../api/endpoints/approvals', () => ({
  decideApproval: vi.fn(),
  getApproval: vi.fn(),
  listApprovals: vi.fn(),
}));

import { decideApproval } from '../../../api/endpoints/approvals';
import { ApprovalCard } from '../ApprovalCard';
import { makeApproval } from '../../../test/approval-fixtures';

const mockDecide = vi.mocked(decideApproval);

beforeEach(() => {
  mockDecide.mockReset();
  mockDecide.mockResolvedValue(makeApproval({ status: 'approved' }));
});

describe('ApprovalCard', () => {
  it('shows title, kind, project, why held and styled findings', () => {
    render(
      <ApprovalCard
        item={makeApproval({
          findings: [
            { kind: 'missing_deliverable', severity: 'error', message: 'No BOM recorded' },
            { kind: 'analysis', severity: 'info', message: 'FEA ran' },
          ],
        })}
      />,
    );
    expect(screen.getByText('Requirements gate')).toBeInTheDocument();
    expect(screen.getByText('Gate')).toBeInTheDocument();
    expect(screen.getByText('project p1')).toBeInTheDocument();
    expect(screen.getByTestId('approval-reason-held')).toHaveTextContent('Two deliverables are missing.');
    expect(screen.getByText('No BOM recorded').closest('li')).toHaveAttribute('data-severity', 'error');
    expect(screen.getByText('FEA ran').closest('li')).toHaveAttribute('data-severity', 'info');
  });

  it('renders gate detail', () => {
    render(<ApprovalCard item={makeApproval()} />);
    const d = screen.getByTestId('approval-detail');
    expect(d).toHaveTextContent('phase requirements');
    expect(d).toHaveTextContent('attempt 1');
    expect(d).toHaveTextContent('2 retries left');
  });

  it('renders tool and arguments for a held tool call', () => {
    render(
      <ApprovalCard
        item={makeApproval({
          id: 'tool:run_d',
          kind: 'tool_call',
          allowed_decisions: ['approve', 'reject'],
          detail: { tool: 'twin.commit_geometry', arguments: { name: 'leg bracket' } },
        })}
      />,
    );
    expect(screen.getByText('twin.commit_geometry')).toBeInTheDocument();
    expect(screen.getByText(/leg bracket/)).toBeInTheDocument();
  });

  it('renders flow proposal changes and design change diff', () => {
    const { unmount } = render(
      <ApprovalCard
        item={makeApproval({
          kind: 'flow_proposal',
          detail: { flow_version_id: 'v2', intent: 'Add a review phase', changes: [{ op: 'add_phase', id: 'review' }] },
        })}
      />,
    );
    expect(screen.getByText('Add a review phase')).toBeInTheDocument();
    expect(screen.getByText(/add_phase/)).toBeInTheDocument();
    unmount();
    render(
      <ApprovalCard
        item={makeApproval({
          kind: 'design_change',
          detail: { change_id: 'c1', diff: { added: 'rib' }, affected: ['wp-1'] },
        })}
      />,
    );
    expect(screen.getByText(/\+ added: rib/)).toBeInTheDocument();
    expect(screen.getByText('wp-1')).toBeInTheDocument();
  });

  it('offers exactly the allowed decisions', () => {
    render(<ApprovalCard item={makeApproval({ allowed_decisions: ['approve', 'reject'] })} />);
    expect(screen.getByRole('button', { name: /approve/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reject/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /rework/i })).not.toBeInTheDocument();
  });

  it('approves without a reason and sends no reviewer name', async () => {
    const user = userEvent.setup();
    const onDecided = vi.fn();
    render(<ApprovalCard item={makeApproval()} onDecided={onDecided} />);
    await user.click(screen.getByRole('button', { name: /approve/i }));
    await user.click(screen.getByRole('button', { name: 'Confirm approve' }));
    expect(mockDecide).toHaveBeenCalledWith('gate:run_abc', { decision: 'approve' });
    expect(onDecided).toHaveBeenCalled();
  });

  it('requires a reason for reject', async () => {
    const user = userEvent.setup();
    render(<ApprovalCard item={makeApproval()} />);
    await user.click(screen.getByRole('button', { name: /reject/i }));
    const confirm = screen.getByRole('button', { name: 'Confirm reject' });
    expect(confirm).toBeDisabled();
    await user.type(screen.getByLabelText('Decision reason'), 'wrong BOM');
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    expect(mockDecide).toHaveBeenCalledWith('gate:run_abc', { decision: 'reject', reason: 'wrong BOM' });
  });

  it('rework needs a phase from rework_targets and a reason', async () => {
    const user = userEvent.setup();
    render(<ApprovalCard item={makeApproval()} />);
    await user.click(screen.getByRole('button', { name: /rework/i }));
    const confirm = screen.getByRole('button', { name: 'Confirm rework' });
    await user.type(screen.getByLabelText('Decision reason'), 'redo it');
    expect(confirm).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('Rework phase'), 'architecture');
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    expect(mockDecide).toHaveBeenCalledWith('gate:run_abc', {
      decision: 'rework',
      reason: 'redo it',
      to_phase: 'architecture',
    });
  });

  it('disables controls and explains when not decidable', () => {
    render(
      <ApprovalCard
        item={makeApproval({ decidable: false, not_decidable_reason: 'Waiting on the elicitation route.' })}
      />,
    );
    expect(screen.getByTestId('not-decidable')).toHaveTextContent('Waiting on the elicitation route.');
    expect(screen.getByRole('button', { name: /approve/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /reject/i })).toBeDisabled();
  });

  it('shows an error when the gateway answers 409', async () => {
    const user = userEvent.setup();
    mockDecide.mockRejectedValueOnce({ response: { status: 409 } });
    render(<ApprovalCard item={makeApproval()} />);
    await user.click(screen.getByRole('button', { name: /approve/i }));
    await user.click(screen.getByRole('button', { name: 'Confirm approve' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/can no longer be decided/);
  });
});
