import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { FieldViewer } from '../FieldViewer';
import { squarePayload } from './fixtures';

// R3F needs WebGL, which jsdom does not have: render the overlays only.
vi.mock('@react-three/fiber', () => ({
  Canvas: () => <div data-testid="r3f-canvas" />,
  useThree: vi.fn(),
}));
vi.mock('@react-three/drei', () => ({ OrbitControls: () => null }));

describe('FieldViewer', () => {
  it('shows a legend with the full-field min and max of the default quantity', () => {
    render(<FieldViewer payload={squarePayload()} />);
    const legend = screen.getByTestId('field-legend');
    expect(legend).toHaveTextContent('Von Mises stress (MPa)');
    expect(screen.getByTestId('legend-max')).toHaveTextContent('120 MPa');
    expect(screen.getByTestId('legend-min')).toHaveTextContent('0 MPa');
    expect(legend).toHaveTextContent('Peak 120 MPa');
  });

  it('switches the colour map quantity and its legend range', () => {
    render(<FieldViewer payload={squarePayload()} />);
    fireEvent.change(screen.getByLabelText('Field quantity'), {
      target: { value: 'displacement_magnitude' },
    });
    expect(screen.getByTestId('legend-max')).toHaveTextContent('0.1 mm');
    expect(screen.getByTestId('field-probe')).toHaveTextContent('Displacement magnitude');
  });

  it('honours a shared range override (compare)', () => {
    render(<FieldViewer payload={squarePayload()} range={{ min: 0, max: 300 }} />);
    expect(screen.getByTestId('legend-max')).toHaveTextContent('300 MPa');
  });

  it('starts the deformation slider at the auto scale and reports changes', () => {
    const onScale = vi.fn();
    render(<FieldViewer payload={squarePayload()} onDeformScaleChange={onScale} />);
    expect(screen.getByTestId('deform-scale')).toHaveTextContent('×7.1');
    fireEvent.change(screen.getByLabelText('Deformation scale'), { target: { value: '2' } });
    expect(onScale).toHaveBeenCalledWith(2);
    expect(screen.getByTestId('deform-scale')).toHaveTextContent('×2');
  });

  it('has no deformation slider for a thermal field', () => {
    render(
      <FieldViewer
        payload={squarePayload({
          displacement: null,
          fields: { temperature: { label: 'Temperature', unit: 'C', values: [20, 30, 40, 50], min: 20, max: 50, peak: null } },
        })}
      />,
    );
    expect(screen.queryByLabelText('Deformation scale')).not.toBeInTheDocument();
    expect(screen.getByTestId('legend-max')).toHaveTextContent('50 C');
  });

  it('keys the load and fixture markers in text, and can hide them', () => {
    render(<FieldViewer payload={squarePayload()} />);
    const legend = screen.getByTestId('field-legend');
    expect(within(legend).getByText(/Fixture Surface1/)).toBeInTheDocument();
    expect(within(legend).getByText(/Load Surface2 · 100 N/)).toBeInTheDocument();
    fireEvent.click(screen.getByTitle('Show load and fixture markers'));
    expect(within(legend).queryByText(/Fixture Surface1/)).not.toBeInTheDocument();
  });

  it('says when the surface was simplified to fit the size cap', () => {
    render(
      <FieldViewer
        payload={squarePayload({ decimation: { applied: true, source_triangle_count: 90000, cell_size_mm: 0.5 } })}
      />,
    );
    expect(screen.getByText(/Simplified from 90,000 triangles/)).toBeInTheDocument();
  });

  it('falls back to a note when the payload has no field to draw', () => {
    render(<FieldViewer payload={squarePayload({ fields: {} })} />);
    expect(screen.getByTestId('field-viewer-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('field-viewer')).not.toBeInTheDocument();
  });
});
