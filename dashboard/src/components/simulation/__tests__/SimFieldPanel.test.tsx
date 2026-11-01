import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '../../../test/test-utils';
import { SimFieldPanel, SimFieldPreview } from '../SimFieldPanel';
import { getSimulationField } from '../../../api/endpoints/simulationResults';
import { squarePayload } from './fixtures';
import type { TwinNode } from '../../../types/twin';

vi.mock('../../../api/endpoints/simulationResults', () => ({
  getSimulationField: vi.fn(),
  getSimulationResults: vi.fn(),
}));
vi.mock('../FieldViewer', () => ({
  default: ({ payload }: { payload: { triangle_count: number } }) => (
    <div data-testid="field-viewer">{payload.triangle_count} triangles</div>
  ),
}));

const mockGet = vi.mocked(getSimulationField);

beforeEach(() => mockGet.mockReset());

describe('SimFieldPanel', () => {
  it('shows the field-not-stored note without fetching when the listing says so', () => {
    render(<SimFieldPanel resultId="r1" hasField={false} />);
    expect(screen.getByTestId('field-not-stored')).toHaveTextContent(/Field not stored/);
    expect(mockGet).not.toHaveBeenCalled();
  });

  it('shows the note when the gateway answers 404 field not stored', async () => {
    mockGet.mockResolvedValue({ status: 'not_stored' });
    render(<SimFieldPanel resultId="r1" />);
    expect(await screen.findByTestId('field-not-stored')).toBeInTheDocument();
  });

  it('renders the viewer for a stored field', async () => {
    mockGet.mockResolvedValue({ status: 'ok', payload: squarePayload() });
    render(<SimFieldPanel resultId="r1" hasField />);
    expect(await screen.findByTestId('field-viewer')).toHaveTextContent('2 triangles');
    expect(mockGet).toHaveBeenCalledWith('r1');
  });

  it('says so when the stored field cannot be drawn', async () => {
    mockGet.mockResolvedValue({ status: 'invalid' });
    render(<SimFieldPanel resultId="r1" hasField />);
    expect(await screen.findByTestId('field-error')).toBeInTheDocument();
  });
});

describe('SimFieldPreview (preview-registry engine)', () => {
  const node = (properties: TwinNode['properties']): TwinNode =>
    ({ id: 'n1', name: 'FEA', type: 'work_product', domain: 'mechanical', status: 'valid', properties, updatedAt: '' }) as TwinNode;

  it('renders nothing for a non-simulation node', () => {
    const { container } = render(<SimFieldPreview node={node({ wp_type: 'cad_model' })} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('fetches only when the node says a field is stored', async () => {
    mockGet.mockResolvedValue({ status: 'ok', payload: squarePayload() });
    render(<SimFieldPreview node={node({ wp_type: 'simulation_result', field_stored: true })} />);
    expect(await screen.findByTestId('field-viewer')).toBeInTheDocument();
  });

  it('shows the note for a result recorded before fields existed', () => {
    render(<SimFieldPreview node={node({ wp_type: 'simulation_result' })} />);
    expect(screen.getByTestId('field-not-stored')).toBeInTheDocument();
    expect(mockGet).not.toHaveBeenCalled();
  });
});
