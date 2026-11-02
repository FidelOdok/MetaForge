import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '../../../test/test-utils';

const view = {
  policy: {
    project_id: 'p1',
    mode: 'off',
    reviewer: 'server',
    reviewer_model: '',
    expires_at: null,
    updated_by: '',
    updated_at: null,
  },
  effective_mode: 'off',
  max_mode: 'advise',
  modes: ['off', 'advise', 'auto_clean', 'auto'],
};
const put = vi.fn();
vi.mock('../../../api/endpoints/approvals', () => ({
  getApprovalPolicy: vi.fn(async () => view),
  putApprovalPolicy: (...args: unknown[]) => put(...args),
}));

import { ApprovalPolicyPanel } from '../ApprovalPolicyPanel';

describe('ApprovalPolicyPanel (FORGE-726)', () => {
  beforeEach(() => {
    put.mockReset();
    put.mockImplementation(async (_id: string, body: { mode: string }) => ({
      ...view,
      policy: { ...view.policy, mode: body.mode },
      effective_mode: body.mode,
    }));
  });

  it('offers only the modes under the gateway cap', async () => {
    render(<ApprovalPolicyPanel projectId="p1" />);
    expect(await screen.findByRole('radio', { name: /^Advise/ })).toBeEnabled();
    expect(screen.getByRole('radio', { name: /^Auto \(clean gates\)/ })).toBeDisabled();
    expect(screen.getByRole('radio', { name: /^Auto: the reviewer decides/ })).toBeDisabled();
  });

  it('saves advise with the reviewer source and model', async () => {
    const user = userEvent.setup();
    render(<ApprovalPolicyPanel projectId="p1" />);
    await user.click(await screen.findByRole('radio', { name: /^Advise/ }));
    await user.type(screen.getByPlaceholderText(/provider:model/), 'anthropic:opus');
    await user.click(screen.getByRole('button', { name: 'Save approval policy' }));
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith('p1', {
        mode: 'advise',
        reviewer: 'server',
        reviewer_model: 'anthropic:opus',
      }),
    );
    expect(await screen.findByTestId('policy-in-force')).toHaveTextContent('advise');
  });
});
