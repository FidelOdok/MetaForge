import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { makeApproval } from '../../../test/approval-fixtures';
import type { ApprovalItem } from '../../../types/approvals';

let items: ApprovalItem[] = [];
vi.mock('../../../hooks/use-approvals', () => ({
  useApprovals: () => ({ data: { items, unscoped_count: 0 } }),
}));

import { ApprovalNotifier } from '../ApprovalNotifier';

function renderAt(path = '/projects') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ApprovalNotifier />
    </MemoryRouter>,
  );
}

const GATE = makeApproval({ id: 'gate:run_1', title: "mech_v1 gate at phase 'design'" });
const HELD = makeApproval({ id: 'tool:run_9', kind: 'tool_call', title: 'Tool call: twin.record_decision' });

describe('ApprovalNotifier (FORGE-723)', () => {
  beforeEach(() => {
    sessionStorage.clear();
    items = [];
  });

  it('shows nothing when nothing is pending', () => {
    renderAt();
    expect(screen.queryByRole('status', { name: 'Approvals waiting' })).toBeNull();
  });

  it('announces pending approvals on any page, with a link to review them', () => {
    items = [GATE, HELD];
    renderAt('/projects');
    const notice = screen.getByRole('status', { name: 'Approvals waiting' });
    expect(notice).toHaveTextContent('2 approvals waiting for you');
    expect(notice).toHaveTextContent("mech_v1 gate at phase 'design'");
    expect(notice).toHaveTextContent('Held write');
    expect(screen.getByRole('link', { name: 'Review approvals' })).toHaveAttribute('href', '/approvals');
  });

  it('is not shown on the Approvals page itself', () => {
    items = [GATE];
    renderAt('/approvals');
    expect(screen.queryByRole('status', { name: 'Approvals waiting' })).toBeNull();
  });

  it('stays dismissed for the same items, and comes back for a new one', () => {
    items = [GATE];
    const { rerender } = renderAt();
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss approvals notice' }));
    expect(screen.queryByRole('status', { name: 'Approvals waiting' })).toBeNull();

    // Moving to another page does not re-announce what was dismissed.
    rerender(
      <MemoryRouter initialEntries={['/runs']}>
        <ApprovalNotifier />
      </MemoryRouter>,
    );
    expect(screen.queryByRole('status', { name: 'Approvals waiting' })).toBeNull();

    items = [HELD, GATE];
    rerender(
      <MemoryRouter initialEntries={['/runs']}>
        <ApprovalNotifier key="new" />
      </MemoryRouter>,
    );
    const notice = screen.getByRole('status', { name: 'Approvals waiting' });
    expect(notice).toHaveTextContent('Tool call: twin.record_decision');
    expect(notice).not.toHaveTextContent("mech_v1 gate at phase 'design'");
  });
});
