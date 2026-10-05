import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MeshConvergenceChart, convergencePlotPoints } from '../MeshConvergenceChart';

const points = [
  { element_size_mm: 2, max_von_mises_mpa: 150, element_count: 12000 },
  { element_size_mm: 8, max_von_mises_mpa: 110, element_count: 900 },
  { element_size_mm: 4, max_von_mises_mpa: 140, element_count: 3500 },
];

describe('convergencePlotPoints', () => {
  it('plots element count coarse to fine when every point has one', () => {
    const { axis, points: plotted } = convergencePlotPoints(points);
    expect(axis).toBe('element_count');
    expect(plotted.map((p) => p.x)).toEqual([900, 3500, 12000]);
    expect(plotted.map((p) => p.y)).toEqual([110, 140, 150]);
  });

  it('falls back to element size when a count is missing', () => {
    const { axis, points: plotted } = convergencePlotPoints([
      { element_size_mm: 2, max_von_mises_mpa: 150 },
      { element_size_mm: 8, max_von_mises_mpa: 110, element_count: 900 },
    ]);
    expect(axis).toBe('element_size_mm');
    expect(plotted.map((p) => p.x)).toEqual([8, 2]);
  });
});

describe('MeshConvergenceChart', () => {
  it('shows the converged verdict with words, not colour alone', () => {
    render(
      <MeshConvergenceChart
        convergence={{
          points,
          converged: true,
          changes: [{ from_element_size_mm: 4, to_element_size_mm: 2, percent_change: 3.4 }],
          recommendation: 'Converged -- refining to 2mm changed max stress by only 3.4%.',
        }}
      />,
    );
    expect(screen.getByTestId('convergence-verdict')).toHaveTextContent('Converged (3.4% last step)');
    expect(screen.getAllByTestId('convergence-point')).toHaveLength(3);
    expect(screen.getByText(/refining to 2mm/)).toBeInTheDocument();
  });

  it('shows not converged, and a hover tooltip with the exact value', () => {
    render(<MeshConvergenceChart convergence={{ points, converged: false }} />);
    expect(screen.getByTestId('convergence-verdict')).toHaveTextContent('Not converged');
    fireEvent.mouseEnter(screen.getAllByTestId('convergence-point')[2]!.parentElement as Element);
    expect(screen.getByText('150 MPa')).toBeInTheDocument();
  });

  it('renders nothing for fewer than two points', () => {
    const { container } = render(
      <MeshConvergenceChart convergence={{ points: points.slice(0, 1), converged: false }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
