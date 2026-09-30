import { beforeEach, describe, it, expect, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, within } from '../../test/test-utils';

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

vi.mock('../../hooks/use-projects', () => ({ useProjects: vi.fn() }));
vi.mock('../../hooks/use-active-project', () => ({
  useActiveProject: () => ({ activeProjectId: null }),
}));
vi.mock('../../hooks/use-runs', () => ({ useCreateRun: vi.fn() }));

// FORGE-395: the wizard reads the flow catalogue from the gateway instead of
// a hand-copied list in this repo, so the test supplies one. The shapes here
// are the API's, and the phase titles are the gateway's real ones -- the old
// local list paraphrased four of them, which is part of what the ticket
// found.
vi.mock('../../hooks/use-design-flows', () => ({
  useDesignFlows: () => ({
    data: {
      defaultFlowId: 'hardware_v1',
      flows: [
        {
          id: 'hardware_v1',
          name: 'Hardware & robotics lifecycle',
          label: 'Hardware & robotics',
          description: 'A multidisciplinary flow for a complete hardware system.',
          version: '1.0.0',
          isDefault: true,
          valid: true,
          violations: [],
          phases: [
            'Intent',
            'Stakeholder Needs',
            'Requirements',
            'Preliminary Feasibility',
            'System Architecture',
            'Concept Selection',
            'Mechanical Design',
            'Electronics Design',
            'Firmware & Control',
            'Simulation & V&V',
            'Manufacturing Prep',
          ].map((title, i) => ({
            id: `p${i}`,
            title,
            objective: 'x',
            expectedArtifacts: [],
            requiredDeliverables: [],
            enforceDeliverables: true,
            disciplines: [],
            gate: { name: `${title} sign-off`, autoApprove: false, criteria: [], enforceConstraints: false, gateId: null },
          })),
        },
        {
          id: 'mech_v1',
          name: 'Mechanical vertical',
          label: 'Mechanical design',
          description: 'A focused flow for a load-bearing part or mechanical subsystem.',
          version: '1.0.0',
          isDefault: false,
          valid: true,
          violations: [],
          phases: [
            'Intent',
            'Stakeholder Needs',
            'Requirements',
            'Preliminary Feasibility',
            'Mechanical Design',
            'Simulation & V&V',
          ].map((title, i) => ({
            id: `m${i}`,
            title,
            objective: 'x',
            expectedArtifacts: [],
            requiredDeliverables: [],
            enforceDeliverables: true,
            disciplines: [],
            gate: { name: `${title} sign-off`, autoApprove: false, criteria: [], enforceConstraints: false, gateId: null },
          })),
        },
      ],
    },
    isLoading: false,
    isError: false,
  }),
}));

import { NewRunPage } from '../NewRunPage';
import { useProjects } from '../../hooks/use-projects';
import { useCreateRun } from '../../hooks/use-runs';

const mockUseProjects = vi.mocked(useProjects);
const mockUseCreateRun = vi.mocked(useCreateRun);

const PROJECTS = [
  { id: 'p1', name: 'Quadruped', status: 'active' },
  { id: 'p2', name: 'Old drone', status: 'archived' },
];

const mutate = vi.fn();

function mockCreateRun(value: Record<string, unknown> = {}) {
  mockUseCreateRun.mockReturnValue({
    mutate,
    reset: vi.fn(),
    isPending: false,
    isError: false,
    ...value,
  } as unknown as ReturnType<typeof useCreateRun>);
}

beforeEach(() => {
  mutate.mockReset();
  navigate.mockReset();
  mockUseProjects.mockReturnValue({
    data: PROJECTS,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useProjects>);
  mockCreateRun();
});

describe('NewRunPage', () => {
  it('renders step one with the hardware lifecycle selected', () => {
    render(<NewRunPage />);
    expect(screen.getByRole('heading', { name: /start a design run/i })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /hardware & robotics/i })).toBeChecked();
    const plan = screen.getByRole('complementary');
    // 'System Architecture', not 'System architecture'. The old local list
    // used the lower-case form, so the wizard named a phase the run does not
    // have -- one of the four paraphrases FORGE-395 found.
    expect(within(plan).getByText('System Architecture')).toBeInTheDocument();
    expect(within(plan).getAllByRole('listitem')).toHaveLength(11);
    expect(screen.getByRole('button', { name: /review run/i })).toBeDisabled();
  });

  it('hides archived projects from the picker', () => {
    render(<NewRunPage />);
    expect(screen.getByRole('option', { name: 'Quadruped' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'Old drone' })).not.toBeInTheDocument();
  });

  it('shows the project load error with a retry', async () => {
    const refetch = vi.fn();
    mockUseProjects.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch,
    } as unknown as ReturnType<typeof useProjects>);
    render(<NewRunPage />);
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Projects could not be loaded. Check the gateway connection or retry.',
    );
    await userEvent.click(screen.getByRole('button', { name: 'retry' }));
    expect(refetch).toHaveBeenCalled();
  });

  it('switches the planned lifecycle when the mechanical flow is chosen', async () => {
    render(<NewRunPage />);
    await userEvent.click(screen.getByRole('radio', { name: /mechanical design/i }));
    const plan = screen.getByRole('complementary');
    expect(within(plan).getAllByRole('listitem')).toHaveLength(6);
    // The real title is 'Electronics Design'; the old local list called it
    // 'Electronics', which is one of the four paraphrases FORGE-395 found.
    expect(within(plan).queryByText('Electronics Design')).not.toBeInTheDocument();
  });

  it('reviews then launches a design-flow run and opens it', async () => {
    const user = userEvent.setup();
    mutate.mockImplementation((_vars, opts) => opts.onSuccess({ id: 'run_42' }));
    render(<NewRunPage />);

    await user.selectOptions(screen.getByLabelText('Project'), 'p1');
    await user.type(screen.getByLabelText('Engineering intent'), '  Lift 2 kg at 100 mm  ');
    await user.click(screen.getByRole('radio', { name: /mechanical design/i }));
    await user.click(screen.getByRole('button', { name: /review run/i }));

    expect(screen.getByRole('heading', { name: /review your run/i })).toBeInTheDocument();
    expect(screen.getByText('6 human checkpoints')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /launch design run/i }));
    expect(mutate).toHaveBeenCalledWith(
      {
        request: { kind: 'design_flow', flow: 'mech_v1', goal: 'Lift 2 kg at 100 mm', project_id: 'p1' },
        start: true,
      },
      expect.any(Object),
    );
    expect(navigate).toHaveBeenCalledWith('/runs/run_42');
  });

  it('warns about duplicates when the launch fails', async () => {
    const user = userEvent.setup();
    render(<NewRunPage />);
    await user.selectOptions(screen.getByLabelText('Project'), 'p1');
    await user.type(screen.getByLabelText('Engineering intent'), 'goal');
    await user.click(screen.getByRole('button', { name: /review run/i }));
    mockCreateRun({ isError: true });
    // Re-render picks up the failed mutation state.
    await user.click(screen.getByRole('button', { name: /edit intent/i }));
    await user.click(screen.getByRole('button', { name: /review run/i }));
    expect(screen.getByRole('alert')).toHaveTextContent(/avoid starting a duplicate/);
  });
});
