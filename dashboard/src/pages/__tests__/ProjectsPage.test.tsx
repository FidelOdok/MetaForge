import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '../../test/test-utils';

vi.mock('../../hooks/use-projects', () => ({
  useProjects: vi.fn(),
  useCreateProject: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

import { ProjectsPage } from '../ProjectsPage';
import { useProjects } from '../../hooks/use-projects';

const mockUseProjects = vi.mocked(useProjects);

describe('ProjectsPage', () => {
  it('shows loading state', () => {
    mockUseProjects.mockReturnValue({ data: undefined, isLoading: true } as ReturnType<typeof useProjects>);
    render(<ProjectsPage />);
    expect(screen.getByText('Loading projects...')).toBeInTheDocument();
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
});
