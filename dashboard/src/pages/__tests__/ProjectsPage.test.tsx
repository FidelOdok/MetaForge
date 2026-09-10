import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '../../test/test-utils';

vi.mock('../../hooks/use-projects', () => ({
  useProjects: vi.fn(),
  useCreateProject: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

const mockUseHealth = vi.fn();
vi.mock('../../hooks/use-health', () => ({
  useHealth: () => mockUseHealth(),
}));

import { ProjectsPage } from '../ProjectsPage';
import { useProjects } from '../../hooks/use-projects';

const mockUseProjects = vi.mocked(useProjects);

describe('ProjectsPage', () => {
  beforeEach(() => {
    mockUseHealth.mockReturnValue({ data: undefined, isLoading: true });
  });

  it('shows loading state', () => {
    mockUseProjects.mockReturnValue({ data: undefined, isLoading: true } as ReturnType<typeof useProjects>);
    const { container } = render(<ProjectsPage />);
    // KC renders SkeletonCard components with animate-pulse (no data-testid)
    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0);
  });

  it('shows empty state', () => {
    mockUseProjects.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useProjects>);
    render(<ProjectsPage />);
    expect(screen.getByText('No projects yet')).toBeInTheDocument();
  });

  it('renders project list', () => {
    mockUseProjects.mockReturnValue({
      data: [
        { id: '1', name: 'Test Project', description: 'Desc', status: 'active', work_products: [], agentCount: 2, lastUpdated: new Date().toISOString(), createdAt: new Date().toISOString() },
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof useProjects>);
    render(<ProjectsPage />);
    expect(screen.getByText('Test Project')).toBeInTheDocument();
  });

  it('does not render fabricated Data Flows / Activity content', () => {
    mockUseProjects.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useProjects>);
    render(<ProjectsPage />);
    // These used to be hardcoded regardless of any real system state.
    expect(screen.queryByText('Data Flows')).not.toBeInTheDocument();
    expect(screen.queryByText('Activity')).not.toBeInTheDocument();
    expect(screen.queryByText('File Save → Twin')).not.toBeInTheDocument();
    expect(screen.queryByText('Node updated: MCU_STM32H7')).not.toBeInTheDocument();
    expect(screen.queryByText(/last sync/)).not.toBeInTheDocument();
  });

  it('renders real per-dependency health from GET /health', () => {
    mockUseProjects.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useProjects>);
    mockUseHealth.mockReturnValue({
      isLoading: false,
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
    expect(screen.getByText('neo4j')).toBeInTheDocument();
    expect(screen.getByText('4ms')).toBeInTheDocument();
    expect(screen.getByText('pgvector')).toBeInTheDocument();
    expect(screen.getByText('degraded')).toBeInTheDocument();
    expect(screen.getByText(/gateway degraded/)).toBeInTheDocument();
  });

  it('renders nothing for System Health when there is no real health data', () => {
    mockUseProjects.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useProjects>);
    mockUseHealth.mockReturnValue({ data: undefined, isLoading: false });
    render(<ProjectsPage />);
    expect(screen.queryByText(/gateway/)).not.toBeInTheDocument();
  });
});
