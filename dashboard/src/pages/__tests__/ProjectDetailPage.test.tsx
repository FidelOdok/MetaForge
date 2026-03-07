import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '../../test/test-utils';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useParams: () => ({ id: 'proj-001' }) };
});

vi.mock('../../hooks/use-projects', () => ({
  useProject: vi.fn(),
}));

import { ProjectDetailPage } from '../ProjectDetailPage';
import { useProject } from '../../hooks/use-projects';

const mockUseProject = vi.mocked(useProject);

describe('ProjectDetailPage', () => {
  it('shows loading state', () => {
    mockUseProject.mockReturnValue({ data: undefined, isLoading: true } as ReturnType<typeof useProject>);
    render(<ProjectDetailPage />);
    expect(screen.getByText('Loading project...')).toBeInTheDocument();
  });

  it('shows not found', () => {
    mockUseProject.mockReturnValue({ data: undefined, isLoading: false } as ReturnType<typeof useProject>);
    render(<ProjectDetailPage />);
    expect(screen.getByText('Project not found')).toBeInTheDocument();
  });

  it('renders project details', () => {
    mockUseProject.mockReturnValue({
      data: {
        id: 'proj-001',
        name: 'Drone FC',
        description: 'Flight controller',
        status: 'active',
        artifacts: [{ id: 'a1', name: 'Schematic', type: 'schematic', status: 'valid', updatedAt: new Date().toISOString() }],
        agentCount: 2,
        lastUpdated: new Date().toISOString(),
        createdAt: new Date().toISOString(),
      },
      isLoading: false,
    } as unknown as ReturnType<typeof useProject>);
    render(<ProjectDetailPage />);
    expect(screen.getByText('Drone FC')).toBeInTheDocument();
    expect(screen.getByText('Schematic')).toBeInTheDocument();
  });
});
