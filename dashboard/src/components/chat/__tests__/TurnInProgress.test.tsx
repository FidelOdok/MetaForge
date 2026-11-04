import { describe, expect, it } from 'vitest';
import { render, screen } from '../../../test/test-utils';
import { TurnInProgress } from '../TurnInProgress';
import type { StreamActivity } from '../activity';

const ev = (kind: string, data: unknown): StreamActivity => ({ kind, data, at: new Date().toISOString() });

describe('TurnInProgress', () => {
  it('says it is thinking before any step arrives', () => {
    render(<TurnInProgress events={[]} startedAt={Date.now() - 4000} />);
    const bubble = screen.getByTestId('turn-in-progress');
    expect(bubble).toHaveTextContent('Thinking…');
    expect(bubble).toHaveTextContent('4s');
    expect(bubble).toHaveAttribute('aria-busy', 'true');
  });

  it('says what it is doing now, in the present tense', () => {
    render(<TurnInProgress events={[ev('agent.action_started', { tool: 'mcp_twin_get_node' })]} startedAt={Date.now()} />);
    expect(screen.getByTestId('turn-in-progress')).toHaveTextContent('Reading the design record…');
  });

  it('lists finished steps and moves on to the answer', () => {
    render(
      <TurnInProgress
        events={[
          ev('agent.action_started', { tool: 'calculix.run_fea' }),
          ev('agent.step', { step: { tool: 'calculix.run_fea', observation: {} } }),
        ]}
        startedAt={Date.now()}
      />,
    );
    const bubble = screen.getByTestId('turn-in-progress');
    expect(bubble).toHaveTextContent('Working out the answer…');
    expect(bubble).toHaveTextContent('Ran a stress check');
  });

  it('says when it is waiting for the person', () => {
    render(
      <TurnInProgress
        events={[ev('tool.approval_requested', { run_id: 'r', tool: 'twin.commit_geometry' })]}
        startedAt={Date.now()}
      />,
    );
    expect(screen.getByTestId('turn-in-progress')).toHaveTextContent('Waiting for your approval');
  });
});
