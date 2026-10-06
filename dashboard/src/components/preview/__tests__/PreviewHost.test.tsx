import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '../../../test/test-utils';
import type { TwinNode } from '../../../types/twin';

vi.mock('../../../api/endpoints/twin', () => ({
  fetchNodeFileText: vi.fn(),
  fetchDerivedPrd: vi.fn(),
  nodeFileUrl: (id: string, download = false) => `/api/v1/twin/nodes/${id}/file${download ? '?download=true' : ''}`,
  getNodeModel: vi.fn(),
}));

// The three.js scene is exercised in the real app; here it is a marker.
vi.mock('../engines/ModelPreview', () => ({
  ModelPreview: ({ engine, format }: { engine: string; format: string }) => (
    <div data-testid="model-preview-stub" data-engine={engine} data-format={format} />
  ),
}));

import { fetchDerivedPrd, fetchNodeFileText } from '../../../api/endpoints/twin';
import { PreviewHost, hasInlinePreview } from '../PreviewHost';
import { clearNodeFileTextCache } from '../useNodeFileText';

// Preview engines load lazily (FORGE-531). Under the full suite's load the
// first render of an engine can take longer than Testing Library's 1 s
// default, which made these tests fail only when run with everything else.
const LAZY_ENGINE = { timeout: 5000 };

const mockFetch = vi.mocked(fetchNodeFileText);
const mockPrd = vi.mocked(fetchDerivedPrd);

let seq = 0;
function node(properties: TwinNode['properties'], extra: Partial<TwinNode> = {}): TwinNode {
  seq += 1;
  return {
    id: `wp-${seq}`,
    name: 'Work product',
    type: 'work_product',
    domain: 'electronics',
    status: 'valid',
    properties,
    updatedAt: '2026-10-05T10:00:00Z',
    ...extra,
  };
}

beforeEach(() => {
  mockFetch.mockReset();
  mockPrd.mockReset();
  clearNodeFileTextCache();
});

describe('PreviewHost: markdown', () => {
  it('renders a PRD as formatted Markdown, never as raw HTML', async () => {
    mockFetch.mockResolvedValue('# Drone PRD\n\nFly for **20 min**.\n\n- GPS\n- Lidar\n\n<script>alert(1)</script>');
    const { container } = render(<PreviewHost node={node({ wp_type: 'prd', format: 'md' })} mode="modal" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Drone PRD' })).toBeInTheDocument();
    expect(screen.getByText('20 min').tagName).toBe('STRONG');
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    // The tag is text, not an element.
    expect(container.querySelector('script')).toBeNull();
    expect(screen.getByText('<script>alert(1)</script>')).toBeInTheDocument();
  });
});

describe('PreviewHost: derived prd (FORGE-528)', () => {
  it('shows the prose with the live requirement table and the revisions it came from', async () => {
    mockFetch.mockResolvedValue('A desk stand.');
    mockPrd.mockResolvedValue({
      project_id: 'p1',
      title: 'Widget PRD',
      markdown:
        '# Widget PRD\n\nA desk stand.\n\n## Requirements\n\n| Ref | Requirement | Limit | Unit |\n|---|---|---|---|\n| CS-W@2 | mass | 12 | g |\n',
      prose_ref: 'PRD-W@1',
      requirement_refs: ['CS-W@2'],
      requirement_count: 1,
      refs: ['PRD-W@1', 'CS-W@2'],
    });
    const prd = node({ wp_type: 'prd', format: 'md', item_key: 'PRD-W', item_revision: 1 }, { projectId: 'p1' });
    render(<PreviewHost node={prd} mode="modal" />);
    const view = await screen.findByTestId('preview-prd', {}, LAZY_ENGINE);
    expect(view).toHaveAttribute('data-derived', 'true');
    expect(mockPrd).toHaveBeenCalledWith('PRD-W@1', 'p1');
    expect(within(view).getByRole('table')).toBeInTheDocument();
    expect(within(view).getAllByText('CS-W@2').length).toBeGreaterThan(0);
    expect(within(view).getByText('PRD-W@1')).toBeInTheDocument();
  });

  it('falls back to the stored prose when the derived view fails', async () => {
    mockFetch.mockResolvedValue('Stored prose only.');
    mockPrd.mockRejectedValue(new Error('down'));
    render(<PreviewHost node={node({ wp_type: 'prd', format: 'md', item_key: 'PRD-W', item_revision: 2 })} mode="modal" />);
    const view = await screen.findByTestId('preview-prd', {}, LAZY_ENGINE);
    await screen.findByText('Stored prose only.');
    expect(view).toHaveAttribute('data-derived', 'false');
  });
});

describe('PreviewHost: tables', () => {
  it('renders a CSV as a table with a row count', async () => {
    mockFetch.mockResolvedValue('name,value\nalpha,1\nbeta,2\n');
    render(<PreviewHost node={node({ wp_type: 'test_result', format: 'csv' })} mode="modal" />);
    const table = await screen.findByRole('table');
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['name', 'value']);
    expect(within(table).getAllByRole('row')).toHaveLength(3);
    expect(screen.getByText(/2 rows · 2 columns/)).toBeInTheDocument();
  });

  it('caps long tables and expands on demand', async () => {
    mockFetch.mockResolvedValue(['n', ...Array.from({ length: 30 }, (_, i) => String(i))].join('\n'));
    render(<PreviewHost node={node({ format: 'csv' })} mode="panel" />);
    await screen.findByRole('table');
    expect(screen.getAllByRole('row')).toHaveLength(26);
    fireEvent.click(screen.getByRole('button', { name: 'Show all 30' }));
    expect(screen.getAllByRole('row')).toHaveLength(31);
  });

  it('renders a BOM with line items and total quantity', async () => {
    mockFetch.mockResolvedValue('Reference,Value,Qty\n"R1,R2",10k,2\nC1,100n,1\n');
    render(<PreviewHost node={node({ wp_type: 'bom', format: 'csv' })} mode="modal" />);
    const bom = await screen.findByTestId('preview-bom', {}, LAZY_ENGINE);
    expect(within(bom).getByText('Line items').nextSibling?.textContent).toBe('2');
    expect(within(bom).getByText('Total quantity').nextSibling?.textContent).toBe('3');
    expect(within(bom).getByText('R1,R2')).toBeInTheDocument();
  });

  it('renders a constraint set as a requirements table', async () => {
    mockFetch.mockResolvedValue(
      '# Constraint set: Power\n\n## max_current (error, electronics)\nStay under budget.\n**Acceptance criteria:** below 2 A\n**Verification method:** bench test\n```python\nctx.i < 2\n```\n',
    );
    render(<PreviewHost node={node({ wp_type: 'constraint_set', format: 'md' })} mode="modal" />);
    const req = await screen.findByTestId('preview-requirements', {}, LAZY_ENGINE);
    expect(within(req).getByText('1 requirement')).toBeInTheDocument();
    expect(within(req).getByText('max_current')).toBeInTheDocument();
    expect(within(req).getByText('error')).toBeInTheDocument();
    expect(within(req).getByText('below 2 A')).toBeInTheDocument();
    expect(within(req).getByText('bench test')).toBeInTheDocument();
  });

  it('falls back to Markdown for a constraint set not in the recorder shape', async () => {
    mockFetch.mockResolvedValue('# Requirements\n\nJust prose.');
    render(<PreviewHost node={node({ wp_type: 'constraint_set', format: 'md' })} mode="modal" />);
    expect(await screen.findByTestId('preview-markdown', {}, LAZY_ENGINE)).toBeInTheDocument();
  });
});

describe('PreviewHost: decision card', () => {
  const md =
    '# Use STM32F4\n\n## Decision\n\nIt has an FPU.\n\n## Alternatives considered\n\n| Option | Why rejected |\n|---|---|\n| RP2040 | No FPU |\n';

  it('shows title, rationale, alternatives and status', async () => {
    mockFetch.mockResolvedValue(md);
    render(<PreviewHost node={node({ wp_type: 'design_decision', format: 'md' }, { status: 'approved' })} mode="modal" />);
    const card = await screen.findByTestId('preview-decision', {}, LAZY_ENGINE);
    expect(within(card).getByRole('heading', { name: 'Use STM32F4' })).toBeInTheDocument();
    expect(within(card).getByText('It has an FPU.')).toBeInTheDocument();
    expect(within(card).getByText('Alternatives considered (1)')).toBeInTheDocument();
    expect(within(card).getByText('RP2040')).toBeInTheDocument();
    expect(within(card).getByText('Rejected: No FPU')).toBeInTheDocument();
    expect(within(card).getByLabelText('Decision status')).toHaveTextContent('approved');
  });

  it('still renders from node properties when the file is missing', async () => {
    mockFetch.mockRejectedValue(new Error('404'));
    render(
      <PreviewHost
        node={node({ wp_type: 'design_decision', format: 'md', rationale: 'Cheaper part.' }, { name: 'Pick LDO' })}
        mode="modal"
      />,
    );
    const card = await screen.findByTestId('preview-decision', {}, LAZY_ENGINE);
    expect(within(card).getByRole('heading', { name: 'Pick LDO' })).toBeInTheDocument();
    expect(within(card).getByText('Cheaper part.')).toBeInTheDocument();
    expect(within(card).getByText('No alternatives were recorded.')).toBeInTheDocument();
  });
});

describe('PreviewHost: JSON and code', () => {
  it('renders JSON as a collapsible tree', async () => {
    mockFetch.mockResolvedValue(JSON.stringify({ pins: { PA0: 'ADC', PA1: 'UART' }, rev: 2 }));
    render(<PreviewHost node={node({ wp_type: 'pinmap', format: 'json' })} mode="modal" />);
    const tree = await screen.findByTestId('preview-json', {}, LAZY_ENGINE);
    expect(within(tree).getByText('"ADC"')).toBeInTheDocument();
    fireEvent.click(within(tree).getByRole('button', { name: /pins/ }));
    expect(within(tree).queryByText('"ADC"')).toBeNull();
  });

  it('shows invalid JSON as text with a warning, not a crash', async () => {
    mockFetch.mockResolvedValue('{not json');
    render(<PreviewHost node={node({ format: 'json' })} mode="modal" />);
    expect(await screen.findByTestId('preview-json-invalid', {}, LAZY_ENGINE)).toBeInTheDocument();
  });

  it('highlights firmware C', async () => {
    mockFetch.mockResolvedValue('#include "main.h"\nint main(void) { return 0; }\n');
    const { container } = render(<PreviewHost node={node({ wp_type: 'firmware_source', format: 'c' })} mode="modal" />);
    const code = await screen.findByTestId('preview-code', {}, LAZY_ENGINE);
    expect(code).toHaveAttribute('data-language', 'c');
    expect(container.querySelector('[data-token="preproc"]')?.textContent).toBe('#include "main.h"');
    expect(container.querySelector('[data-token="keyword"]')?.textContent).toBe('return');
  });
});

describe('PreviewHost: vector engines', () => {
  it('renders a Gerber layer as an image', async () => {
    mockFetch.mockResolvedValue('%FSLAX26Y26*%\n%MOMM*%\n%ADD10C,0.5*%\nD10*\nX0Y0D02*\nX10000000Y0D01*\nM02*');
    render(<PreviewHost node={node({ wp_type: 'gerber', format: 'gtl' }, { name: 'Top copper' })} mode="modal" />);
    const fig = await screen.findByTestId('preview-gerber', {}, LAZY_ENGINE);
    expect(within(fig).getByRole('img', { name: 'Top copper' }).getAttribute('src')).toMatch(/^data:image\/svg\+xml/);
  });

  it('falls back with a download when the Gerber renderer cannot draw it', async () => {
    mockFetch.mockResolvedValue('M02*');
    render(<PreviewHost node={node({ wp_type: 'gerber', format: 'gbr' })} mode="modal" />);
    const fb = await screen.findByTestId('preview-unavailable', {}, LAZY_ENGINE);
    expect(fb).toHaveAttribute('data-engine', 'gerber');
    expect(within(fb).getByRole('link', { name: /Download \.gbr/ })).toBeInTheDocument();
  });

  it('renders a DXF as an image', async () => {
    mockFetch.mockResolvedValue(
      ['0', 'SECTION', '2', 'ENTITIES', '0', 'LINE', '8', '0', '10', '0', '20', '0', '11', '10', '21', '5', '0', 'ENDSEC', '0', 'EOF', ''].join('\n'),
    );
    render(<PreviewHost node={node({ wp_type: 'technical_drawing', format: 'dxf' }, { name: 'Gasket' })} mode="modal" />);
    const fig = await screen.findByTestId('preview-dxf', {}, LAZY_ENGINE);
    expect(within(fig).getByRole('img', { name: 'Gasket' })).toBeInTheDocument();
    expect(within(fig).getByText(/1 entities/)).toBeInTheDocument();
  });
});

describe('PreviewHost: fallbacks', () => {
  it('names the missing engine for an unknown format and offers a download', () => {
    render(<PreviewHost node={node({ format: 'weird' })} mode="modal" />);
    const fb = screen.getByTestId('preview-unavailable');
    expect(within(fb).getByText('No preview engine for .weird files')).toBeInTheDocument();
    expect(within(fb).getByRole('link', { name: /Download \.weird/ })).toHaveAttribute('href', expect.stringContaining('download=true'));
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('says the KiCad viewer is not available instead of dumping the file', () => {
    render(<PreviewHost node={node({ wp_type: 'schematic', format: 'kicad_sch' })} mode="modal" />);
    const fb = screen.getByTestId('preview-unavailable');
    expect(within(fb).getByText('KiCad viewer not available')).toBeInTheDocument();
    expect(within(fb).getByRole('link', { name: /Download \.kicad_sch/ })).toBeInTheDocument();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it.each([
    ['cad_model', 'step'],
    ['cad_model', 'stl'],
    ['manufacturing_file', '3mf'],
    ['prd', 'pdf'],
    ['firmware_source', 'hex'],
    ['gerber', 'zip'],
    ['design_sketch', 'png'],
  ])('never fetches a binary (%s .%s) as text', (wpType, format) => {
    render(<PreviewHost node={node({ wp_type: wpType, format })} mode="modal" />);
    expect(mockFetch).not.toHaveBeenCalled();
    expect(screen.queryByTestId('preview-code')).toBeNull();
  });

  it('reports a missing file plainly', async () => {
    mockFetch.mockRejectedValue(new Error('404'));
    render(<PreviewHost node={node({ wp_type: 'prd', format: 'md' })} mode="modal" />);
    expect(await screen.findByText('No file stored for this work product yet.')).toBeInTheDocument();
  });
});

describe('PreviewHost: 3D, FEA and modes', () => {
  it('sends STEP to the CAD engine and STL to the mesh engine', async () => {
    const { unmount } = render(<PreviewHost node={node({ wp_type: 'cad_model', format: 'step' })} mode="modal" />);
    expect(await screen.findByTestId('model-preview-stub', {}, LAZY_ENGINE)).toHaveAttribute('data-engine', 'cad3d');
    unmount();
    render(<PreviewHost node={node({ wp_type: 'cad_model', format: 'stl' })} mode="compact" />);
    const stub = await screen.findByTestId('model-preview-stub', {}, LAZY_ENGINE);
    expect(stub).toHaveAttribute('data-engine', 'mesh3d');
    expect(stub).toHaveAttribute('data-format', 'stl');
  });

  it('renders the FEA summary card for a simulation result in every mode', () => {
    const sim = node({ wp_type: 'simulation_result', max_von_mises_mpa: 182.4, max_displacement_mm: 0.0123, load_case: 'tip' });
    render(<PreviewHost node={sim} mode="compact" />);
    expect(screen.getByTestId('fea-result-section')).toHaveTextContent('182.4');
  });

  it('defers 3D, robot, PDF and HTML to other surfaces in the inspector panel', () => {
    expect(hasInlinePreview(node({ wp_type: 'cad_model', format: 'step' }), 'panel')).toBe(false);
    expect(hasInlinePreview(node({ wp_type: 'robot_description', format: 'urdf' }), 'panel')).toBe(false);
    expect(hasInlinePreview(node({ format: 'pdf' }), 'panel')).toBe(false);
    expect(hasInlinePreview(node({ wp_type: 'prd', format: 'md' }), 'panel')).toBe(true);
    expect(hasInlinePreview(node({ wp_type: 'cad_model', format: 'step' }), 'compact')).toBe(true);
    const { container } = render(<PreviewHost node={node({ wp_type: 'cad_model', format: 'step' })} mode="panel" />);
    expect(container).toBeEmptyDOMElement();
  });

  it('offers to open a robot description in the 3D viewer from the modal', () => {
    const onOpen = vi.fn();
    render(<PreviewHost node={node({ wp_type: 'robot_description', format: 'urdf' })} mode="modal" onOpenInViewer={onOpen} />);
    fireEvent.click(screen.getByRole('button', { name: /Open in 3D Viewer/ }));
    expect(onOpen).toHaveBeenCalled();
  });
});
