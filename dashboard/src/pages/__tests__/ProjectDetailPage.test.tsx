import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '../../test/test-utils';

const mockNavigate = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useParams: () => ({ id: 'proj-001' }), useNavigate: () => mockNavigate };
});

const mockUpdateMutate = vi.fn();
const mockDeleteMutate = vi.fn();

vi.mock('../../hooks/use-projects', () => ({
  useProject: vi.fn(),
  useUpdateProject: () => ({ mutate: mockUpdateMutate, isPending: false }),
  useDeleteProject: () => ({ mutate: mockDeleteMutate, isPending: false }),
}));

import { ProjectDetailPage } from '../ProjectDetailPage';
import { useProject } from '../../hooks/use-projects';

const mockUseProject = vi.mocked(useProject);

describe('ProjectDetailPage', () => {
  it('shows loading state', () => {
    mockUseProject.mockReturnValue({ data: undefined, isLoading: true } as ReturnType<typeof useProject>);
    const { container } = render(<ProjectDetailPage />);
    // KC renders animate-pulse skeleton elements (no data-testid)
    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0);
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
        work_products: [{ id: 'a1', name: 'Schematic', type: 'schematic', status: 'valid', updatedAt: new Date().toISOString() }],
        agentCount: 2,
        lastUpdated: new Date().toISOString(),
        createdAt: new Date().toISOString(),
      },
      isLoading: false,
    } as unknown as ReturnType<typeof useProject>);
    render(<ProjectDetailPage />);
    // project name appears in breadcrumb + heading
    expect(screen.getAllByText('Drone FC').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Schematic')).toBeInTheDocument();
  });

  describe('rename', () => {
    beforeEach(() => {
      mockUseProject.mockReturnValue({
        data: {
          id: 'proj-001',
          name: 'Drone FC',
          description: 'Flight controller',
          status: 'active',
          work_products: [],
          agentCount: 0,
          lastUpdated: new Date().toISOString(),
          createdAt: new Date().toISOString(),
        },
        isLoading: false,
      } as unknown as ReturnType<typeof useProject>);
      mockUpdateMutate.mockClear();
    });

    it('opens a pre-filled edit form and submits the new name', () => {
      render(<ProjectDetailPage />);
      fireEvent.click(screen.getByLabelText('Rename project'));

      const nameInput = screen.getByLabelText('Project name') as HTMLInputElement;
      expect(nameInput.value).toBe('Drone FC');

      fireEvent.change(nameInput, { target: { value: 'Drone FC v2' } });
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));

      expect(mockUpdateMutate).toHaveBeenCalledWith(
        { id: 'proj-001', payload: { name: 'Drone FC v2', description: 'Flight controller' } },
        expect.anything(),
      );
    });

    it('cancel exits the form without saving', () => {
      render(<ProjectDetailPage />);
      fireEvent.click(screen.getByLabelText('Rename project'));
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
      expect(screen.queryByLabelText('Project name')).not.toBeInTheDocument();
      expect(mockUpdateMutate).not.toHaveBeenCalled();
    });
  });

  describe('delete', () => {
    beforeEach(() => {
      mockUseProject.mockReturnValue({
        data: {
          id: 'proj-001',
          name: 'Drone FC',
          description: '',
          status: 'active',
          work_products: [],
          agentCount: 0,
          lastUpdated: new Date().toISOString(),
          createdAt: new Date().toISOString(),
        },
        isLoading: false,
      } as unknown as ReturnType<typeof useProject>);
      mockDeleteMutate.mockClear();
      mockNavigate.mockClear();
    });

    it('confirms before deleting', () => {
      render(<ProjectDetailPage />);
      fireEvent.click(screen.getByLabelText('Delete project'));
      expect(screen.getByText('Delete project')).toBeInTheDocument();
      expect(mockDeleteMutate).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
      expect(mockDeleteMutate).toHaveBeenCalledWith('proj-001', expect.anything());
    });

    it('cancel dismisses the confirmation without deleting', () => {
      render(<ProjectDetailPage />);
      fireEvent.click(screen.getByLabelText('Delete project'));
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
      expect(screen.queryByText('Delete project')).not.toBeInTheDocument();
      expect(mockDeleteMutate).not.toHaveBeenCalled();
    });
  });
});
