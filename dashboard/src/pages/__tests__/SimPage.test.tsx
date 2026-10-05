import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '../../test/test-utils';
import { SimPage } from '../SimPage';
import type { SimulationResult } from '../../types/simulationResult';

vi.mock('../../hooks/use-active-project', () => ({
  useActiveProject: () => ({ activeProjectId: 'p1' }),
}));
vi.mock('../../hooks/use-load-cases', () => ({
  useLoadCases: () => ({ data: [], isLoading: false }),
  useCreateLoadCase: () => ({ mutate: vi.fn(), isPending: false }),
  useNamedFaces: () => ({ mutate: vi.fn(), data: undefined, isPending: false }),
}));
vi.mock('../../components/simulation/LoadCaseDialog', () => ({ LoadCaseDialog: () => null }));

const results: SimulationResult[] = [
  {
    id: 'new',
    name: 'Rev 2 FEA',
    maxVonMisesMpa: 45,
    maxDisplacementMm: 0.2,
    loadCase: 'static_1g',
    meshStats: null,
    projectId: 'p1',
    createdAt: '2026-10-02T10:00:00Z',
    updatedAt: '2026-10-02T10:00:00Z',
    hasField: false,
    analysedGeometry: { node_id: 'g2', name: 'Bracket', revision: 'B' },
    fixtures: [{ label: 'Surface1', kind: 'fixture' }],
    meshConvergence: {
      converged: true,
      points: [
        { element_size_mm: 4, max_von_mises_mpa: 40, element_count: 1000 },
        { element_size_mm: 2, max_von_mises_mpa: 45, element_count: 8000 },
      ],
    },
  },
  {
    id: 'old',
    name: 'Rev 1 FEA',
    maxVonMisesMpa: 60,
    maxDisplacementMm: 0.3,
    loadCase: 'static_1g',
    meshStats: null,
    projectId: 'p1',
    createdAt: '2026-09-20T10:00:00Z',
    updatedAt: '2026-09-20T10:00:00Z',
  },
];

vi.mock('../../hooks/use-simulation-results', () => ({
  useSimulationResults: () => ({ data: results, isLoading: false }),
  useSimulationField: () => ({ data: undefined, isLoading: false, isError: false }),
}));

describe('SimPage results in 3D (FORGE-532)', () => {
  it('opens the newest result by default with what it analysed', () => {
    render(<SimPage />);
    const detail = screen.getByTestId('result-detail');
    expect(detail).toHaveTextContent('Rev 2 FEA');
    expect(detail).toHaveTextContent('Geometry · Bracket rev B');
    expect(detail).toHaveTextContent('Fixtures · Surface1');
    // Recorded without a field: the numbers stay, the 3D view says why it is absent.
    expect(screen.getByTestId('field-not-stored')).toBeInTheDocument();
    expect(screen.getByTestId('mesh-convergence-chart')).toBeInTheDocument();
  });

  it('switches the 3D view when another result is opened', () => {
    render(<SimPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Open Rev 1 FEA in 3D' }));
    expect(screen.getByTestId('result-detail')).toHaveTextContent('Rev 1 FEA');
    expect(screen.queryByTestId('mesh-convergence-chart')).not.toBeInTheDocument();
  });

  it('compares two selected results numerically and in 3D', () => {
    render(<SimPage />);
    fireEvent.click(screen.getByLabelText('Select Rev 1 FEA for comparison'));
    fireEvent.click(screen.getByLabelText('Select Rev 2 FEA for comparison'));
    expect(screen.getByTestId('results-compare')).toBeInTheDocument();
    // Rev 2 has no stored field, so the 3D compare explains that instead.
    expect(screen.getByTestId('compare-field-not-stored')).toHaveTextContent('Rev 2 FEA');
    expect(screen.queryByTestId('result-detail')).not.toBeInTheDocument();
  });
});
