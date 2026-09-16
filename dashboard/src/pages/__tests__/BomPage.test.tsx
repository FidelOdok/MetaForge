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
        { id: 'b1', designator: 'U1', partNumber: 'STM32F405', description: 'MCU', manufacturer: 'STM', quantity: 1, unitPrice: 8.5, priceCurrency: 'USD', status: 'available', category: 'IC', projectId: 'p1', imageUrl: null, purchaseUrl: null, datasheetUrl: null, footprint: null, cadModelUrl: null },
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof useBom>);
    render(<BomPage />);
    expect(screen.getByText('U1')).toBeInTheDocument();
    expect(screen.getByText('STM32F405')).toBeInTheDocument();
    expect(screen.getByText('$8.50')).toBeInTheDocument();
  });

  it('renders a purchase link, datasheet link, and non-USD price correctly', () => {
    mockUseBom.mockReturnValue({
      data: [
        {
          id: 'b2',
          designator: 'U2',
          partNumber: 'MP2459',
          description: 'Buck converter',
          manufacturer: 'MPS',
          quantity: 1,
          unitPrice: 1.21,
          priceCurrency: 'GBP',
          status: 'available',
          category: 'IC',
          projectId: 'p1',
          imageUrl: 'https://example.com/mp2459.png',
          purchaseUrl: 'https://www.mouser.co.uk/en/ProductDetail/x',
          datasheetUrl: 'https://example.com/mp2459.pdf',
          footprint: null,
          cadModelUrl: null,
        },
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof useBom>);
    render(<BomPage />);
    expect(screen.getByText('£1.21')).toBeInTheDocument();
    const purchaseLink = screen.getByRole('link', { name: 'MP2459' });
    expect(purchaseLink).toHaveAttribute('href', 'https://www.mouser.co.uk/en/ProductDetail/x');
    const datasheetLink = screen.getByTitle('Open datasheet');
    expect(datasheetLink).toHaveAttribute('href', 'https://example.com/mp2459.pdf');
    expect(screen.getByAltText('MP2459')).toHaveAttribute('src', 'https://example.com/mp2459.png');
  });
});
