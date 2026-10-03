import { describe, it, expect, vi, beforeEach } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '../../test/test-utils';
import type { HarnessRun } from '../../types/run';

vi.mock('../../hooks/use-runs', () => ({
  useRuns: vi.fn(),
}));

vi.mock('../../hooks/use-active-project', () => ({
  useActiveProject: vi.fn(),
}));

import { RunsPage } from '../RunsPage';
import { useRuns } from '../../hooks/use-runs';
import { useActiveProject } from '../../hooks/use-active-project';

const mockUseRuns = vi.mocked(useRuns);
const mockActiveProject = vi.mocked(useActiveProject);

const PROJECT = '11111111-1111-1111-1111-111111111111';

beforeEach(() => {
  // Default: no project selected, which is the pre-existing behaviour every
  // test below was written against.
  mockActiveProject.mockReturnValue({ activeProjectId: null } as unknown as ReturnType<
    typeof useActiveProject
  >);
});

const RUNNING_RUN: HarnessRun = {
  id: 'run_1',
  status: 'running',
  request: { goal: 'design a bracket' },
  createdAt: Date.now() / 1000 - 60,
  updatedAt: Date.now() / 1000,
  history: ['queued', 'running'],
};

const DONE_RUN: HarnessRun = {
  id: 'run_2',
  status: 'completed',
  request: { goal: 'quadruped leg' },
  createdAt: Date.now() / 1000 - 600,
  updatedAt: Date.now() / 1000 - 500,
  history: ['queued', 'running', 'completed'],
};

/** `useRuns` now returns `{ runs, unscopedCount }` rather than a bare array.
 *  Tests pass the array they care about and this wraps it, so a shape change
 *  stays in one place instead of being spelled out at every call site. */
function mockRuns(value: { data?: HarnessRun[]; isLoading?: boolean; isError?: boolean }) {
  const { data, ...rest } = value;
  mockUseRuns.mockReturnValue({
    refetch: vi.fn(),
    isFetching: false,
    data: data === undefined ? undefined : { runs: data, unscopedCount: 0 },
    ...rest,
  } as unknown as ReturnType<typeof useRuns>);
}

describe('RunsPage', () => {
  it('shows loading state', () => {
    mockRuns({ data: undefined, isLoading: true });
    render(<RunsPage />);
    expect(screen.getByText('Loading runs…')).toBeInTheDocument();
  });

  it('shows an error state when runs cannot be loaded', () => {
    mockRuns({ data: undefined, isLoading: false, isError: true });
    render(<RunsPage />);
    expect(screen.getByText('Runs could not be loaded')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /connection settings/i })).toHaveAttribute('href', '/settings');
  });

  it('links "New design run" to the flow wizard instead of creating a bare run', () => {
    // Regression (MET-671): a button that POSTed a bare { goal } with no
    // `flow` id left a permanently-stuck run. Launch now goes through the
    // /runs/new wizard, which always sends a design-flow request.
    mockRuns({ data: [], isLoading: false });
    render(<RunsPage />);
    expect(screen.getByRole('link', { name: /new design run/i })).toHaveAttribute('href', '/runs/new');
    expect(screen.queryByRole('button', { name: /new run/i })).not.toBeInTheDocument();
  });

  it('shows the empty state pointing at the wizard and CLI/MCP', () => {
    mockRuns({ data: [], isLoading: false });
    render(<RunsPage />);
    expect(screen.getByText('No runs yet')).toBeInTheDocument();
    expect(screen.getByText(/CLI or MCP client/i)).toBeInTheDocument();
  });

  it('renders run rows when runs exist', () => {
    mockRuns({ data: [RUNNING_RUN], isLoading: false });
    render(<RunsPage />);
    expect(screen.getByText('design a bracket')).toBeInTheDocument();
    expect(screen.getByText('run_1')).toBeInTheDocument();
  });

  it('filters by search text and status', async () => {
    const user = userEvent.setup();
    mockRuns({ data: [RUNNING_RUN, DONE_RUN], isLoading: false });
    render(<RunsPage />);

    await user.type(screen.getByLabelText('Search runs'), 'quadruped');
    expect(screen.queryByText('design a bracket')).not.toBeInTheDocument();
    expect(screen.getByText('quadruped leg')).toBeInTheDocument();

    await user.clear(screen.getByLabelText('Search runs'));
    await user.selectOptions(screen.getByLabelText('Run status'), 'running');
    expect(screen.getByText('design a bracket')).toBeInTheDocument();
    expect(screen.queryByText('quadruped leg')).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText('Run status'), 'failed');
    expect(screen.getByText('No matching runs')).toBeInTheDocument();
  });
});

describe('RunsPage project scoping', () => {
  it('asks only for the active project\'s runs', () => {
    mockActiveProject.mockReturnValue({ activeProjectId: PROJECT } as unknown as ReturnType<
      typeof useActiveProject
    >);
    mockRuns({ data: [RUNNING_RUN], isLoading: false });
    render(<RunsPage />);
    // The whole bug: this page listed every project's runs in one table,
    // and a run's project lived only inside its request blob.
    expect(mockUseRuns).toHaveBeenCalledWith(PROJECT);
  });

  it('asks for everything when no project is selected', () => {
    mockRuns({ data: [RUNNING_RUN], isLoading: false });
    render(<RunsPage />);
    expect(mockUseRuns).toHaveBeenCalledWith(undefined);
  });

  it('says how many runs were hidden for having no project', () => {
    // The part that matters. Filtering them out silently is how a run
    // disappears and the absence reads as "there are none" -- five of the
    // seventeen runs on the dev gateway carry no project.
    mockActiveProject.mockReturnValue({ activeProjectId: PROJECT } as unknown as ReturnType<
      typeof useActiveProject
    >);
    mockUseRuns.mockReturnValue({
      refetch: vi.fn(),
      isFetching: false,
      isLoading: false,
      data: { runs: [RUNNING_RUN], unscopedCount: 5 },
    } as unknown as ReturnType<typeof useRuns>);
    render(<RunsPage />);
    const note = screen.getByTestId('runs-unscoped');
    expect(note).toHaveTextContent('5 runs not shown');
    expect(note).toHaveTextContent('no project recorded');
  });

  it('says nothing when nothing is hidden', () => {
    mockRuns({ data: [RUNNING_RUN], isLoading: false });
    render(<RunsPage />);
    expect(screen.queryByTestId('runs-unscoped')).not.toBeInTheDocument();
  });
});
