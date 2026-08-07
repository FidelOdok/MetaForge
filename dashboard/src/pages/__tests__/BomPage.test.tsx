import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '../../test/test-utils';

vi.mock('../../hooks/use-bom', () => ({
  useBom: vi.fn(),
}));

const mockUseActiveProject = vi.fn(() => ({
  activeProjectId: null as string | null,
  activeProject: undefined,
  setActiveProjectId: vi.fn(),
  projects: [] as unknown[],
}));
vi.mock('../../hooks/use-active-project', () => ({
  useActiveProject: () => mockUseActiveProject(),
}));

import { BomPage } from '../BomPage';
import { useBom } from '../../hooks/use-bom';

const mockUseBom = vi.mocked(useBom);

describe('BomPage', () => {
  it('shows loading state', () => {
    mockUseBom.mockReturnValue({ data: undefined, isLoading: true } as ReturnType<typeof useBom>);
    const { container } = render(<BomPage />);
    // KC renders animate-pulse skeleton rows (no data-testid)
    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0);
  });

  it('shows empty state', () => {
    mockUseBom.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useBom>);
    render(<BomPage />);
    expect(screen.getByText('No components')).toBeInTheDocument();
  });

  it('does not claim no project is loaded when one actually is', () => {
    mockUseActiveProject.mockReturnValue({
      activeProjectId: 'p1',
      activeProject: undefined,
      setActiveProjectId: vi.fn(),
      projects: [],
    });
    mockUseBom.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useBom>);
    render(<BomPage />);
    expect(screen.getByText('This project has no BOM components yet.')).toBeInTheDocument();
    expect(screen.queryByText(/when a project is loaded/)).not.toBeInTheDocument();
  });

  it('renders BOM table', () => {
    mockUseBom.mockReturnValue({
      data: [
        { id: 'b1', designator: 'U1', partNumber: 'STM32F405', description: 'MCU', manufacturer: 'STM', quantity: 1, unitPrice: 8.5, status: 'available', category: 'IC', projectId: 'p1' },
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof useBom>);
    render(<BomPage />);
    expect(screen.getByText('U1')).toBeInTheDocument();
    expect(screen.getByText('STM32F405')).toBeInTheDocument();
  });
});
