import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor, within } from '../../test/test-utils';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useSearchParams: () => [new URLSearchParams(), vi.fn()] };
});

const validateMutate = vi.fn();
const saveMutate = vi.fn();
const validateState: Record<string, unknown> = { data: undefined, isPending: false };
const saveState: Record<string, unknown> = { isPending: false, isError: false, error: null };

vi.mock('../../hooks/use-design-flows', () => ({
  useDesignFlows: () => ({
    data: { defaultFlowId: 'hardware_v1', flows: [FLOW] },
    isLoading: false,
    isError: false,
  }),
  useValidateFlowEdit: () => ({ mutate: validateMutate, ...validateState }),
  useSaveFlowVersion: () => ({ mutate: saveMutate, reset: vi.fn(), ...saveState }),
}));

import { FlowEditorPage } from '../FlowEditorPage';

const PHASES = [
  {
    id: 'requirements',
    title: 'Requirements',
    objective: 'x',
    expectedArtifacts: ['constraint_set', 'prd'],
    requiredDeliverables: ['constraint_set'],
    enforceDeliverables: true,
    disciplines: [],
    gate: {
      name: 'Requirements sign-off',
      autoApprove: false,
      criteria: [],
      enforceConstraints: false,
      gateId: null,
    },
  },
  {
    id: 'simulation',
    title: 'Simulation & V&V',
    objective: 'y',
    expectedArtifacts: ['simulation_result'],
    requiredDeliverables: ['simulation_result'],
    enforceDeliverables: true,
    disciplines: [],
    gate: {
      name: 'Release sign-off',
      autoApprove: false,
      criteria: [],
      enforceConstraints: false,
      gateId: null,
    },
  },
];

const FLOW = {
  id: 'hardware_v1',
  name: 'Hardware & robotics lifecycle',
  label: 'Hardware & robotics',
  description: 'A multidisciplinary flow.',
  version: '1.0.0',
  isDefault: true,
  valid: true,
  violations: [],
  phases: PHASES,
};

beforeEach(() => {
  validateMutate.mockReset();
  saveMutate.mockReset();
  validateState.data = undefined;
  validateState.isPending = false;
  saveState.isPending = false;
  saveState.isError = false;
  saveState.error = null;
});

describe('FlowEditorPage', () => {
  it('loads the template phases for editing', () => {
    render(<FlowEditorPage />);
    const list = screen.getByRole('list', { name: 'Phases' });
    expect(within(list).getAllByRole('listitem')).toHaveLength(2);
    expect(within(list).getByText('Requirements')).toBeInTheDocument();
  });

  it('says the flow is unchanged rather than leaving validity ambiguous', () => {
    // A validity indicator that only appears when something is wrong makes
    // "nothing shown" mean both "valid" and "not checked".
    render(<FlowEditorPage />);
    expect(screen.getByTestId('validation-panel')).toHaveTextContent('Unchanged from the template');
  });

  it('cannot save a flow that has not been edited', () => {
    render(<FlowEditorPage />);
    expect(screen.getByRole('button', { name: /save as new version/i })).toBeDisabled();
  });

  it('asks the server to validate an edit rather than deciding locally', async () => {
    // The rules live on the server. A second copy here would be a second
    // answer to "is this flow valid", and the copy that disagrees lets an
    // unstartable flow through the UI.
    render(<FlowEditorPage />);
    await userEvent.click(screen.getByRole('button', { name: 'Remove simulation' }));
    await waitFor(() => expect(validateMutate).toHaveBeenCalled(), { timeout: 2000 });
    const [payload] = validateMutate.mock.calls[0] as [{ phases: { id: string }[] }];
    expect(payload.phases.map((p) => p.id)).toEqual(['requirements']);
  });

  it('refuses to save while an invariant is broken', async () => {
    validateState.data = {
      valid: false,
      violations: ["release-gate-exists: no phase carries a human-answered release gate"],
    };
    render(<FlowEditorPage />);
    await userEvent.click(screen.getByRole('button', { name: 'Remove simulation' }));

    const violations = await screen.findByRole('list', { name: 'Invariant violations' });
    expect(violations).toHaveTextContent('release-gate-exists');
    expect(screen.getByRole('button', { name: /save as new version/i })).toBeDisabled();
  });

  it('reorders phases', async () => {
    render(<FlowEditorPage />);
    await userEvent.click(screen.getByRole('button', { name: 'Move simulation earlier' }));
    const items = within(screen.getByRole('list', { name: 'Phases' })).getAllByRole('listitem');
    expect(items[0]).toHaveTextContent('Simulation & V&V');
  });

  it('saves a valid edit and says nothing runs until it is approved', async () => {
    validateState.data = { valid: true, violations: [] };
    saveMutate.mockImplementation((_body, opts) =>
      opts.onSuccess({
        versionId: 'flowv_abc123',
        approvalId: 'run_1',
        baseTemplateId: 'hardware_v1',
        baseVersion: '1.0.0',
        status: 'proposed',
        origin: 'edited',
        changes: ["removed phase 'simulation' (Simulation & V&V)"],
        flow: FLOW,
        valid: true,
        violations: [],
      }),
    );

    render(<FlowEditorPage />);
    await userEvent.click(screen.getByRole('button', { name: 'Remove simulation' }));
    await userEvent.click(screen.getByRole('button', { name: /save as new version/i }));

    const saved = await screen.findByTestId('saved-version');
    expect(saved).toHaveTextContent('flowv_abc123');
    expect(within(saved).getByRole('list', { name: 'Changes' })).toHaveTextContent(
      "removed phase 'simulation'",
    );
    expect(saved).toHaveTextContent('Nothing runs on this flow until it is approved');
  });

  it('surfaces the server refusal rather than a generic failure', async () => {
    validateState.data = { valid: true, violations: [] };
    saveState.isError = true;
    saveState.error = {
      response: { data: { detail: 'gates-enforce-what-they-require: gate ... (phase 1)' } },
    };
    render(<FlowEditorPage />);
    await userEvent.click(screen.getByRole('button', { name: 'Remove simulation' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'gates-enforce-what-they-require',
    );
  });
});
