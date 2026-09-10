import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '../../test/test-utils';
import type { HarnessRun } from '../../types/run';

vi.mock('../../hooks/use-runs', () => ({
  useRuns: vi.fn(),
}));

import { RunsPage } from '../RunsPage';
import { useRuns } from '../../hooks/use-runs';

const mockUseRuns = vi.mocked(useRuns);

const RUNNING_RUN: HarnessRun = {
  id: 'run_1',
  status: 'running',
  request: { goal: 'design a bracket' },
  createdAt: Date.now() / 1000 - 60,
  updatedAt: Date.now() / 1000,
  history: ['queued', 'running'],
};

describe('RunsPage', () => {
  it('shows loading state', () => {
    mockUseRuns.mockReturnValue({ data: undefined, isLoading: true } as unknown as ReturnType<typeof useRuns>);
    render(<RunsPage />);
    expect(screen.getByText('Loading…')).toBeInTheDocument();
  });

  it('does not render a "New run" button that would create an orphaned, permanently-stuck run', () => {
    // Regression (MET-671): the button called createRun with a bare
    // { goal: "demo run" } and no `flow` id. The backend's create_run only
    // ever drives a run to completion via the design-flow executor, which
    // requires a `flow` id -- a bare goal starts the run and then NOTHING
    // ever transitions it out of "running". Clicking the button left a
    // permanently-stuck, unrecoverable "Running" entry with no cancel
    // affordance anywhere. The dashboard has no real flow-selection UI, so
    // the button had no legitimate action to perform -- removed rather than
    // wired to fabricated data.
    mockUseRuns.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useRuns>);
    render(<RunsPage />);
    expect(screen.queryByRole('button', { name: /new run/i })).not.toBeInTheDocument();
  });

  it('shows an honest empty state pointing at the CLI/MCP as the real way to launch a run', () => {
    mockUseRuns.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useRuns>);
    render(<RunsPage />);
    expect(screen.getByText(/this page observes them live/i)).toBeInTheDocument();
  });

  it('renders run rows when runs exist', () => {
    mockUseRuns.mockReturnValue({ data: [RUNNING_RUN], isLoading: false } as unknown as ReturnType<typeof useRuns>);
    render(<RunsPage />);
    expect(screen.getByText('design a bracket')).toBeInTheDocument();
    expect(screen.getByText('run_1')).toBeInTheDocument();
  });
});
