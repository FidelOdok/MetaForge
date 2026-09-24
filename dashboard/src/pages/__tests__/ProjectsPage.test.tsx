import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '../../test/test-utils';
import type { Project } from '../../types/project';
import type { HarnessRun } from '../../types/run';

const mockCreateMutate = vi.fn();
vi.mock('../../hooks/use-projects', () => ({
  useProjects: vi.fn(),
  useCreateProject: vi.fn(() => ({ mutate: mockCreateMutate, isPending: false, isError: false, reset: vi.fn() })),
}));

const mockUseRuns = vi.fn();
vi.mock('../../hooks/use-runs', () => ({
  useRuns: () => mockUseRuns(),
}));

const mockUseHealth = vi.fn();
vi.mock('../../hooks/use-health', () => ({
  useHealth: () => mockUseHealth(),
}));

import { ProjectsPage } from '../ProjectsPage';
import { useProjects } from '../../hooks/use-projects';

const mockUseProjects = vi.mocked(useProjects);
type ProjectsResult = ReturnType<typeof useProjects>;

function project(overrides: Partial<Project>): Project {
  return {
    id: 'p',
    name: 'Project',
    description: '',
    status: 'active',
    work_products: [],
    agentCount: 0,
    lastUpdated: new Date().toISOString(),
    createdAt: new Date().toISOString(),
    ...overrides,
  };
}

function setProjects(data: Project[] | undefined, extra: Record<string, unknown> = {}) {
  mockUseProjects.mockReturnValue({
    data,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
    ...extra,
  } as unknown as ProjectsResult);
}

const now = Date.now();
const sampleProjects = [
  project({
    id: '1',
    name: 'Rover',
    description: 'Inspection rover',
    status: 'active',
    lastUpdated: new Date(now - 60_000).toISOString(),
    work_products: [{ id: 'w1', name: 'Chassis', type: 'cad_model', status: 'valid', updatedAt: new Date(now).toISOString() }],
    agentCount: 2,
  }),
  project({ id: '2', name: 'Antenna', status: 'draft', lastUpdated: new Date(now).toISOString() }),
  project({ id: '3', name: 'Legacy FC', status: 'archived', lastUpdated: new Date(now - 3_600_000).toISOString() }),
];

function cardNames(): string[] {
  const library = screen.getByRole('region', { name: /Project library/ });
  return Array.from(library.querySelectorAll('.project-card h3')).map((h) => h.textContent ?? '');
}

describe('ProjectsPage', () => {
  beforeEach(() => {
    mockUseHealth.mockReturnValue({ data: undefined, isLoading: false, refetch: vi.fn() });
    mockUseRuns.mockReturnValue({ data: [], isLoading: false, isError: false, refetch: vi.fn() });
    mockCreateMutate.mockClear();
  });

  it('renders the heading, eyebrow and new project action', () => {
    setProjects([]);
    render(<ProjectsPage />);
    expect(screen.getByText('YOUR ENGINEERING WORKSPACE')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Projects.');
    expect(screen.getByRole('button', { name: /New project/ })).toBeInTheDocument();
  });

  it('shows loading state', () => {
    mockUseProjects.mockReturnValue({ data: undefined, isLoading: true, isError: false } as unknown as ProjectsResult);
    render(<ProjectsPage />);
    expect(screen.getByText('Loading your workspace')).toBeInTheDocument();
  });

  it('shows the first-project empty state', () => {
    setProjects([]);
    render(<ProjectsPage />);
    expect(screen.getByText('Start with an engineering intent')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create a project' })).toBeInTheDocument();
  });

  it('computes workspace metrics from real data', () => {
    setProjects(sampleProjects);
    mockUseRuns.mockReturnValue({
      data: [
        { id: 'r1', status: 'running', request: {}, history: [] },
        { id: 'r2', status: 'queued', request: {}, history: [] },
        { id: 'r3', status: 'awaiting_approval', request: { goal: 'Size the motor' }, approvalReason: 'Gate: mechanical', history: [] },
      ] as unknown as HarnessRun[],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    render(<ProjectsPage />);
    const metrics = within(screen.getByRole('region', { name: 'Workspace summary' }));
    expect(metrics.getByText('Active projects').closest('a')).toHaveTextContent('01');
    expect(metrics.getByText('Runs in progress').closest('a')).toHaveTextContent('02');
    expect(metrics.getByText('Awaiting review').closest('a')).toHaveTextContent('01');
    expect(metrics.getByText('Work products').closest('a')).toHaveTextContent('01');
    expect(metrics.getByText('Awaiting review').closest('a')).toHaveAttribute('href', '/approvals');

    // Review queue lists the paused run
    expect(screen.getByText('Size the motor')).toBeInTheDocument();
    expect(screen.getByText('Gate: mechanical')).toBeInTheDocument();
    expect(screen.getByText('Size the motor').closest('a')).toHaveAttribute('href', '/runs/r3');

    // Recent artifacts
    expect(screen.getByText('Chassis')).toBeInTheDocument();
  });

  it('shows the connection notice and unavailable states when the gateway errors', () => {
    const refetch = vi.fn();
    setProjects(undefined, { isError: true, refetch });
    mockUseRuns.mockReturnValue({ data: undefined, isLoading: false, isError: true, refetch: vi.fn() });
    render(<ProjectsPage />);
    expect(screen.getByText('Connect your engineering gateway')).toBeInTheDocument();
    expect(screen.getByText('Your workspace, connected.')).toBeInTheDocument();
    expect(screen.getByText('Review data unavailable')).toBeInTheDocument();
    expect(screen.getByText('Waiting for gateway')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Set up connection/ })).toHaveAttribute('href', '/settings');
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }));
    expect(refetch).toHaveBeenCalled();
  });

  it('searches, filters by status and sorts the library', () => {
    setProjects(sampleProjects);
    render(<ProjectsPage />);
    // default sort: recently updated
    expect(cardNames()).toEqual(['Antenna', 'Rover', 'Legacy FC']);

    fireEvent.change(screen.getByLabelText('Sort projects'), { target: { value: 'name' } });
    expect(cardNames()).toEqual(['Antenna', 'Legacy FC', 'Rover']);

    fireEvent.click(screen.getByRole('button', { name: 'Draft' }));
    expect(cardNames()).toEqual(['Antenna']);
    expect(screen.getByText('1 of 3 projects')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /All projects/ }));
    fireEvent.change(screen.getByLabelText('Search projects'), { target: { value: 'inspection' } });
    expect(cardNames()).toEqual(['Rover']);

    fireEvent.change(screen.getByLabelText('Search projects'), { target: { value: 'zzz' } });
    expect(screen.getByText('No matching projects')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(cardNames()).toHaveLength(3);
  });

  it('switches between grid and list views', () => {
    setProjects(sampleProjects);
    const { container } = render(<ProjectsPage />);
    expect(container.querySelector('.project-collection.grid')).not.toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'List view' }));
    expect(container.querySelector('.project-collection.list')).not.toBeNull();
    expect(screen.getByRole('button', { name: 'List view' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('project cards link to the project detail page', () => {
    setProjects(sampleProjects);
    render(<ProjectsPage />);
    expect(screen.getByText('Rover').closest('a')).toHaveAttribute('href', '/projects/1');
  });

  it('creates a project from the dialog', () => {
    setProjects([]);
    render(<ProjectsPage />);
    fireEvent.click(screen.getByRole('button', { name: /New project/ }));
    fireEvent.change(screen.getByLabelText('Project name'), { target: { value: '  Rover  ' } });
    fireEvent.change(screen.getByLabelText(/Description/), { target: { value: 'Inspect pipes' } });
    fireEvent.click(screen.getByRole('button', { name: /Create project/, hidden: true }));
    expect(mockCreateMutate).toHaveBeenCalledWith(
      { name: 'Rover', description: 'Inspect pipes' },
      expect.anything(),
    );
  });

  it('does not render fabricated Data Flows / Activity content', () => {
    setProjects([]);
    render(<ProjectsPage />);
    expect(screen.queryByText('Data Flows')).not.toBeInTheDocument();
    expect(screen.queryByText('File Save → Twin')).not.toBeInTheDocument();
    expect(screen.queryByText('Node updated: MCU_STM32H7')).not.toBeInTheDocument();
    expect(screen.queryByText(/last sync/)).not.toBeInTheDocument();
  });

  it('renders real per-dependency health from GET /health', () => {
    setProjects([]);
    mockUseHealth.mockReturnValue({
      isLoading: false,
      refetch: vi.fn(),
      data: {
        status: 'degraded',
        timestamp: new Date().toISOString(),
        uptime_seconds: 123,
        version: '0.1.0',
        components: [
          { name: 'neo4j', status: 'healthy', latency_ms: 4.2, message: null },
          { name: 'pgvector', status: 'degraded', latency_ms: null, message: 'slow' },
        ],
      },
    });
    render(<ProjectsPage />);
    expect(screen.getByText('System health')).toBeInTheDocument();
    expect(screen.getByText('neo4j')).toBeInTheDocument();
    expect(screen.getByText('healthy · 4ms')).toBeInTheDocument();
    expect(screen.getByText('pgvector')).toBeInTheDocument();
  });

  it('omits System health when there is no real health data', () => {
    setProjects([]);
    render(<ProjectsPage />);
    expect(screen.queryByText('System health')).not.toBeInTheDocument();
  });
});
