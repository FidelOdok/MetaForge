import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '../../../test/test-utils';

const get = vi.fn();
const post = vi.fn();
vi.mock('../../../api/client', () => ({ default: { get: (...a: unknown[]) => get(...a), post: (...a: unknown[]) => post(...a) } }));

import { RunSupervisionPanel } from '../RunSupervisionPanel';

const supervised = {
  active: true,
  status: 'waiting_for_person',
  reason: '',
  chain: ['run_1', 'run_2'],
  budgets: { max_actions: 20, max_restarts: 3 },
  counters: { actions: 2, restarts: 1 },
  log: [{ at: '', run_id: 'run_2', action: 'restart', detail: 'design: restarted from run_1' }],
};

describe('RunSupervisionPanel (FORGE-739)', () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  it('offers to supervise an unsupervised run', async () => {
    get.mockRejectedValue({ response: { status: 404 } });
    post.mockResolvedValue({ data: { ...supervised, status: 'watching', chain: ['run_1'] } });
    const user = userEvent.setup();
    render(<RunSupervisionPanel runId="run_1" ended={false} />);
    await user.click(await screen.findByRole('button', { name: 'Supervise this run' }));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/runs/run_1/supervise', {}, expect.anything()));
    expect(await screen.findByTestId('supervision-status')).toHaveTextContent('Watching the run');
  });

  it('shows the status, budgets and log, and can stop', async () => {
    get.mockResolvedValue({ data: supervised });
    post.mockResolvedValue({ data: { ...supervised, active: false, status: 'stopped', reason: 'stopped from the dashboard' } });
    const user = userEvent.setup();
    render(<RunSupervisionPanel runId="run_2" ended={false} />);
    expect(await screen.findByTestId('supervision-status')).toHaveTextContent('Waiting for a person at a gate');
    expect(screen.getByTestId('run-supervision')).toHaveTextContent('Restarts 1 of 3');
    expect(screen.getByTestId('run-supervision')).toHaveTextContent('restarted from run_1');
    await user.click(screen.getByRole('button', { name: 'Stop supervising' }));
    expect(await screen.findByTestId('supervision-status')).toHaveTextContent('Stopped');
  });
});
