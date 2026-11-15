import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ValueSources } from '../ValueSources';

const config = {
  process: { id: 'fdm', material: 'pla', machine: 'mi_cr10' },
  context: {
    components: {
      parts: [
        { id: 'ring-a', name: 'NeoPixel Ring 12', geometry: { envelope: [36.8, 36.8, 6.7], derived_from: 'library', source: 'library ring v2' } },
        { id: 'touch-a', name: 'Touch electrode', geometry: { envelope: [25, 0.1, 25], size_basis: 'typical', source: 'design choice' } },
      ],
    },
    machines: { machines: [{ id: 'mi_cr10', name: 'My CR-10 V3 (home)', derived_from: 'library', source: 'library mi_cr10 v1', fromProcess: ['tolerance'], defaulted: [] }] },
  },
};

describe('ValueSources', () => {
  it('lists each value with where it came from and counts the library ones', () => {
    render(<ValueSources config={config} />);
    expect(screen.getByTestId('value-sources')).toHaveTextContent('2 of 3 from the library');
    expect(screen.getByText('NeoPixel Ring 12')).toBeInTheDocument();
    expect(screen.getByText('library ring v2')).toBeInTheDocument();
    expect(screen.getByText('Estimate')).toBeInTheDocument();
    expect(screen.getByText('My CR-10 V3 (home)')).toBeInTheDocument();
  });

  it('renders nothing when the studio has nothing sourced to show', () => {
    const { container } = render(<ValueSources config={{ process: { id: 'fdm' } }} />);
    expect(container).toBeEmptyDOMElement();
  });
});
