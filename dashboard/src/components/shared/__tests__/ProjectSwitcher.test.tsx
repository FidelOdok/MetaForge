import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '../../../test/test-utils';
import { useProjectStore } from '../../../store/project-store';

const mockUseProjects = vi.fn();
vi.mock('../../../hooks/use-projects', () => ({
  useProjects: () => mockUseProjects(),
}));

const mockUseBom = vi.fn();
vi.mock('../../../hooks/use-bom', () => ({
  useBom: (projectId?: string) => mockUseBom(projectId),
}));

import { ProjectSwitcher } from '../ProjectSwitcher';
import { BomPage } from '../../../pages/BomPage';

const PROJECTS = [
  {
    id: 'p-old',
    name: 'Older Project',
    description: '',
    status: 'active',
    work_products: [],
    agentCount: 0,
    lastUpdated: '2026-01-01T00:00:00Z',
    createdAt: '2026-01-01T00:00:00Z',
  },
  {
    id: 'p-new',
    name: 'Newer Project',
    description: '',
    status: 'active',
    work_products: [],
    agentCount: 0,
    lastUpdated: '2026-05-22T00:00:00Z',
    createdAt: '2026-05-22T00:00:00Z',
  },
];

describe('ProjectSwitcher', () => {
  beforeEach(() => {
    useProjectStore.setState({ activeProjectId: null, hasSelected: false });
    mockUseProjects.mockReturnValue({ data: PROJECTS, isLoading: false });
  });

  it('auto-selects the most-recently-updated project on first render', () => {
    render(<ProjectSwitcher />);
    const select = screen.getByLabelText(/active project/i) as HTMLSelectElement;
    expect(select.value).toBe('p-new');
    expect(useProjectStore.getState().activeProjectId).toBe('p-new');
  });

  it('lists "All projects" plus every project, newest first', () => {
    render(<ProjectSwitcher />);
    const select = screen.getByLabelText(/active project/i) as HTMLSelectElement;
    const labels = Array.from(select.options).map((o) => o.textContent);
    expect(labels).toEqual(['All projects', 'Newer Project', 'Older Project']);
  });

  it('switching updates the shared store so other pages see it', () => {
    render(<ProjectSwitcher />);
    const select = screen.getByLabelText(/active project/i) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: 'p-old' } });
    expect(useProjectStore.getState().activeProjectId).toBe('p-old');
  });

  it('switching back to "All projects" is a deliberate, sticky choice', () => {
    render(<ProjectSwitcher />);
    const select = screen.getByLabelText(/active project/i) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: '' } });
    expect(useProjectStore.getState().activeProjectId).toBeNull();
    expect(useProjectStore.getState().hasSelected).toBe(true);
  });

  it('respects an already-active project instead of re-auto-selecting', () => {
    useProjectStore.setState({ activeProjectId: 'p-old', hasSelected: true });
    render(<ProjectSwitcher />);
    const select = screen.getByLabelText(/active project/i) as HTMLSelectElement;
    expect(select.value).toBe('p-old');
  });

  // MET-653: the above tests only prove the STORE value changes on switch —
  // not that an already-mounted consumer page actually re-renders with the
  // new project's content. Mount a real page alongside the switcher, sharing
  // the real store (only its data hook is mocked), and switch while mounted.
  describe('propagation to an already-mounted consumer page', () => {
    beforeEach(() => {
      mockUseBom.mockImplementation((projectId?: string) => ({
        data: [
          {
            designator: projectId ?? 'none',
            partNumber: 'PN-1',
            description: 'd',
            manufacturer: 'm',
            quantity: 1,
            unitPrice: 1,
            status: 'valid',
          },
        ],
        isLoading: false,
      }));
    });

    it('switching the active project re-renders the mounted page with the new project scope', () => {
      useProjectStore.setState({ activeProjectId: 'p-old', hasSelected: true });
      render(
        <>
          <ProjectSwitcher />
          <BomPage />
        </>,
      );

      expect(mockUseBom).toHaveBeenLastCalledWith('p-old');
      expect(screen.getByText('p-old')).toBeInTheDocument();
      expect(screen.queryByText('p-new')).not.toBeInTheDocument();

      const select = screen.getByLabelText(/active project/i) as HTMLSelectElement;
      fireEvent.change(select, { target: { value: 'p-new' } });

      expect(mockUseBom).toHaveBeenLastCalledWith('p-new');
      expect(screen.getByText('p-new')).toBeInTheDocument();
      expect(screen.queryByText('p-old')).not.toBeInTheDocument();
    });
  });
});
