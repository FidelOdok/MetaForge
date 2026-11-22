import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor, within } from '../../../test/test-utils';

const deployment = {
  max_mode: 'auto',
  allow_human_authority: true,
  allow_release: false,
  hourly_caps: [
    { tool: 'twin.render_concept_image', per_hour: 20, set_by: 'METAFORGE_AUTO_APPROVE_IMAGE_PER_HOUR' },
    { tool: 'cadquery.execute_script', per_hour: 60, set_by: 'METAFORGE_AUTO_APPROVE_SCRIPT_PER_HOUR' },
  ],
  approval_ttl_seconds: 3600,
  set_in: 'env',
};

const projectView = {
  policy: {
    project_id: 'p1',
    mode: 'auto_clean',
    reviewer: 'server',
    reviewer_model: '',
    expires_at: null,
    updated_by: 'user:me',
    updated_at: null,
    allow_human_authority: false,
    allow_release: null,
  },
  effective_mode: 'advise',
  max_mode: 'advise',
  modes: ['off', 'advise', 'auto_clean', 'auto'],
  account_max_mode: 'advise',
  limited_by: 'account',
  delegation: {
    human_authority: { allowed: false, decided_by: 'project', reason: 'off here' },
    release: { allowed: false, decided_by: 'deployment', reason: 'off everywhere' },
  },
  deployment,
};

const accountView = {
  policy: {
    account: 'acct',
    max_mode: 'advise',
    allow_human_authority: null,
    allow_release: null,
    updated_by: '',
    updated_at: null,
  },
  max_mode: 'advise',
  modes: ['off', 'advise', 'auto_clean', 'auto'],
  allow_human_authority: true,
  allow_release: false,
  deployment,
};

const putProject = vi.fn();
const putAccount = vi.fn();
vi.mock('../../../api/endpoints/approvals', () => ({
  getApprovalPolicy: vi.fn(async () => projectView),
  putApprovalPolicy: (...a: unknown[]) => putProject(...a),
  getAccountApprovalPolicy: vi.fn(async () => accountView),
  putAccountApprovalPolicy: (...a: unknown[]) => putAccount(...a),
}));

import { AccountApprovalSettings } from '../AccountApprovalSettings';
import { ApprovalPolicyPanel } from '../ApprovalPolicyPanel';

beforeEach(() => {
  putProject.mockReset().mockResolvedValue(projectView);
  putAccount.mockReset().mockResolvedValue(accountView);
});

describe('Approval settings in layers (FORGE-904)', () => {
  it('says which layer holds the project below its choice, and why a mode is unavailable', async () => {
    render(<ApprovalPolicyPanel projectId="p1" />);
    expect(await screen.findByTestId('policy-in-force')).toHaveTextContent(
      "This project chose auto_clean, held there by your account’s limit",
    );
    expect(screen.getByRole('radio', { name: /^Auto \(clean gates\).*above your account’s limit/ })).toBeDisabled();
    expect(screen.getByTestId('delegation-human_authority')).toHaveTextContent('not allowed, by this project');
    expect(screen.getByTestId('delegation-release')).toHaveTextContent('not allowed, by this MetaForge');
  });

  it('saves the project turning delegation off', async () => {
    const user = userEvent.setup();
    render(<ApprovalPolicyPanel projectId="p1" />);
    await screen.findByTestId('project-delegation');
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Fix the release baseline and approve a release' }),
      'off',
    );
    await user.click(screen.getByRole('radio', { name: /^Advise/ }));
    await user.click(screen.getByRole('button', { name: 'Save approval policy' }));
    await waitFor(() =>
      expect(putProject).toHaveBeenCalledWith(
        'p1',
        expect.objectContaining({ mode: 'advise', allow_human_authority: false, allow_release: false }),
      ),
    );
  });

  it('shows the deployment’s limits read only, with the caps', async () => {
    render(<ApprovalPolicyPanel projectId="p1" />);
    const note = await screen.findByTestId('deployment-limits');
    expect(note).toHaveTextContent('highest mode Auto (test runs)');
    expect(note).toHaveTextContent('agents releasing not allowed');
    expect(note).toHaveTextContent('20 concept images, 60 CadQuery scripts');
    expect(note).toHaveTextContent('60 minutes');
    expect(within(note).queryByRole('button')).toBeNull();
  });

  it('says plainly when approvals are off on this MetaForge, and stays quiet when they are on', async () => {
    const { unmount } = render(<ApprovalPolicyPanel projectId="p1" />);
    await screen.findByTestId('policy-in-force');
    expect(screen.queryByTestId('approvals-off')).toBeNull();
    expect(screen.getByTestId('deployment-limits')).toHaveTextContent('Approvals: on');
    unmount();
    const off = { ...projectView, deployment: { ...deployment, approvals_off: true } };
    const { getApprovalPolicy } = await import('../../../api/endpoints/approvals');
    vi.mocked(getApprovalPolicy).mockResolvedValueOnce(off as never);
    render(<ApprovalPolicyPanel projectId="p2" />);
    const notice = await screen.findByTestId('approvals-off');
    expect(notice).toHaveTextContent("Approvals are off on this MetaForge (METAFORGE_APPROVALS=off)");
    expect(notice).toHaveTextContent('placing an order still wait for a person');
  });

  it('sets the account’s ceiling and switches, never above the deployment', async () => {
    const user = userEvent.setup();
    render(<AccountApprovalSettings />);
    expect(await screen.findByTestId('account-in-force')).toHaveTextContent(
      'Your projects may use at most Advise. Agents deciding for you: allowed. Agents releasing: not allowed.',
    );
    expect(screen.getByRole('option', { name: 'Follow this MetaForge (not allowed)' })).toBeInTheDocument();
    await user.click(screen.getByRole('radio', { name: 'At most Off' }));
    await user.selectOptions(screen.getByRole('combobox', { name: 'Agents may decide for you' }), 'off');
    await user.click(screen.getByRole('button', { name: 'Save approval settings' }));
    await waitFor(() =>
      expect(putAccount).toHaveBeenCalledWith({
        max_mode: 'off',
        allow_human_authority: false,
        allow_release: null,
      }),
    );
  });
});
