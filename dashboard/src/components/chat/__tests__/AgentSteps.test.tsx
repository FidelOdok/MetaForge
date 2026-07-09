import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '../../../test/test-utils';
import { AgentSteps } from '../AgentSteps';
import type { AgentStep } from '../../../types/chat';

let mockSteps: Record<string, AgentStep[]> = {};

vi.mock('../../../store/chat-store', () => ({
  useChatStore: (selector: (s: { agentSteps: Record<string, AgentStep[]> }) => unknown) =>
    selector({ agentSteps: mockSteps }),
}));

function toolStep(over: Partial<AgentStep> = {}): AgentStep {
  return {
    index: 0,
    thought: 'I should look up the node',
    tool: 'twin.get_node',
    arguments: { id: 'n1' },
    observation: { status: 'ok', name: 'Base Plate' },
    error: null,
    final: false,
    ...over,
  };
}

describe('AgentSteps', () => {
  beforeEach(() => {
    mockSteps = {};
  });

  it('renders nothing when there are no steps', () => {
    const { container } = render(<AgentSteps threadId="t1" />);
    expect(container.firstChild).toBeNull();
  });

  it('renders a tool step with its tool id', () => {
    mockSteps = { t1: [toolStep()] };
    render(<AgentSteps threadId="t1" />);
    expect(screen.getByText('twin.get_node')).toBeInTheDocument();
  });

  it('hides the final answer-bearing step (no tool, final)', () => {
    mockSteps = {
      t1: [{ index: 0, thought: '', tool: null, arguments: null, observation: 'answer', error: null, final: true }],
    };
    const { container } = render(<AgentSteps threadId="t1" />);
    expect(container.firstChild).toBeNull();
  });

  it('expands to show arguments and result on click', () => {
    mockSteps = { t1: [toolStep()] };
    render(<AgentSteps threadId="t1" />);
    fireEvent.click(screen.getByText('twin.get_node'));
    expect(screen.getByText('arguments')).toBeInTheDocument();
    expect(screen.getByText('result')).toBeInTheDocument();
  });

  it('shows an error step', () => {
    mockSteps = { t1: [toolStep({ error: 'boom-failure', observation: null })] };
    render(<AgentSteps threadId="t1" />);
    fireEvent.click(screen.getByText('twin.get_node'));
    // The error value renders (collapsed preview + expanded field).
    expect(screen.getAllByText('boom-failure').length).toBeGreaterThan(0);
  });
});
