import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '../../../test/test-utils';
import apiClient from '../../../api/client';
import { installSampleAdapter, setSampleModeForTests, SAMPLE_PROJECT_ID } from '../../../lib/sample-workspace';
import { TwinAgentChat } from '../TwinAgentChat';
import type { TwinNode } from '../../../types/twin';

const pcb: TwinNode = {
  id: 'sample-pcb',
  name: 'flight-controller.pcb',
  type: 'work_product',
  domain: 'electronics',
  status: 'valid',
  properties: { wp_type: 'cad_model' },
  updatedAt: '2026-09-22T12:00:00Z',
};

// The drawer runs against the offline sample workspace, which exercises the
// real hooks and API client end to end without a gateway.
describe('TwinAgentChat', () => {
  beforeEach(() => {
    setSampleModeForTests(true);
    installSampleAdapter(apiClient);
  });

  afterEach(() => setSampleModeForTests(false));

  function renderChat(onApplied = vi.fn()) {
    render(
      <TwinAgentChat projectId={SAMPLE_PROJECT_ID} projectName="Drone FC · sample" node={pcb} onApplied={onApplied} />,
    );
    return onApplied;
  }

  it('opens the latest project thread with its messages and the node as context', async () => {
    renderChat();
    expect(await screen.findByText('5V rail over budget')).toBeInTheDocument();
    expect(await screen.findByText(/peak draw is 2\.61 A/)).toBeInTheDocument();
    expect(screen.getByText('electronics agent')).toBeInTheDocument();
    expect(screen.getByTitle('Inspect context')).toHaveTextContent('node');
    expect(await screen.findByText('Scripted sample assistant')).toBeInTheDocument();
  });

  it('sends a turn and shows the scripted reply', async () => {
    renderChat();
    const box = await screen.findByLabelText('Message to engineering agent');
    await waitFor(() => expect(box).not.toBeDisabled());
    fireEvent.change(box, { target: { value: 'What changed?' } });
    const send = screen.getByRole('button', { name: 'Send message' });
    await waitFor(() => expect(send).not.toBeDisabled());
    fireEvent.click(send);
    expect(await screen.findByText(/Turn finished/)).toBeInTheDocument();
    expect(await screen.findByText(/Scripted sample response/)).toBeInTheDocument();
    // The context snapshot appended to the turn is not echoed back.
    expect(screen.queryByText(/context snapshot/)).not.toBeInTheDocument();
  });

  it('lists pending changes and approves one from the review dialog', async () => {
    const onApplied = renderChat();
    fireEvent.click(await screen.findByRole('button', { name: /Changes · 1/ }));
    expect(await screen.findByText(/Reduce telemetry from 10 Hz to 1 Hz/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Inspect proposal ↗' }));
    const dialog = screen.getByRole('dialog', { hidden: true });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Approve & apply', hidden: true }));
    await waitFor(() => expect(onApplied).toHaveBeenCalled());
    expect(await screen.findByText(/Proposal approved/)).toBeInTheDocument();
  });

  it('opens option popovers from the toolbar', async () => {
    renderChat();
    fireEvent.click(screen.getByTitle('Slash commands'));
    expect(screen.getByText('Prompt shortcuts. No command runs until you send.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /\/propose/ }));
    expect(screen.getByLabelText('Message to engineering agent')).toHaveValue(
      'Propose a reviewable change to the selected component.',
    );
    fireEvent.click(screen.getByTitle('Response detail'));
    expect(screen.getByText('Plus raw received events and timestamps')).toBeInTheDocument();
  });

  it('asks for a project when none is active', () => {
    render(<TwinAgentChat projectId={null} onApplied={vi.fn()} />);
    expect(screen.getByText('Choose a project to begin a conversation.')).toBeInTheDocument();
    expect(screen.getByLabelText('Message to engineering agent')).toBeDisabled();
  });
});
