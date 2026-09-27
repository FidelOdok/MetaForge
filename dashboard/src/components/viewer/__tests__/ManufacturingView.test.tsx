import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { render } from '../../../test/test-utils';
import { ManufacturingView } from '../ManufacturingView';
import type { TwinNode } from '../../../types/twin';

/**
 * The manufacturing tab answers "could this be built", which is a different
 * question from the other tabs and the one the prime rule cares about.
 *
 * Its whole value is that absence is visible. A list of the files you have
 * can never show that the fabrication package is missing its pick-and-place,
 * so the assertions below are mostly about the empty half.
 */

function node(id: string, wpType: string, name: string, status = 'approved'): TwinNode {
  return {
    id,
    name,
    type: 'work_product',
    domain: 'electronics',
    status,
    properties: { wp_type: wpType },
    updatedAt: '2026-09-27T00:00:00Z',
  } as TwinNode;
}

describe('ManufacturingView', () => {
  it('counts produced outputs against the full package', () => {
    render(<ManufacturingView nodes={[node('1', 'gerber', 'board.gbr')]} onSelect={vi.fn()} />);
    expect(screen.getByText(/1 of \d+ outputs produced/)).toBeInTheDocument();
  });

  it('shows nothing produced on an empty twin', () => {
    render(<ManufacturingView nodes={[]} onSelect={vi.fn()} />);
    expect(screen.getByText(/0 of \d+ outputs produced/)).toBeInTheDocument();
  });

  it('names every missing output rather than hiding it', () => {
    // The point of the view: you cannot tell a package is incomplete from a
    // list of what it contains.
    render(<ManufacturingView nodes={[]} onSelect={vi.fn()} />);
    for (const label of ['Gerbers', 'Pick and place', 'Bill of materials', 'Technical drawing']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getAllByText('Not produced').length).toBeGreaterThan(3);
  });

  it('tells you which tool produces a missing output', () => {
    // "Not produced" with no next step is just a shrug.
    render(<ManufacturingView nodes={[]} onSelect={vi.fn()} />);
    expect(screen.getByText('kicad.export_gerber')).toBeInTheDocument();
    expect(screen.getByText('freecad.export_model')).toBeInTheDocument();
  });

  it('lists the real artefacts when they exist, and drops the hint', () => {
    render(
      <ManufacturingView
        nodes={[node('1', 'gerber', 'top-copper.gbr'), node('2', 'gerber', 'soldermask.gbr')]}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText('top-copper.gbr')).toBeInTheDocument();
    expect(screen.getByText('soldermask.gbr')).toBeInTheDocument();
    expect(screen.getByText('2 in the twin')).toBeInTheDocument();
    expect(screen.queryByText('kicad.export_gerber')).not.toBeInTheDocument();
  });

  it('selects the node when an artefact is clicked', async () => {
    const onSelect = vi.fn();
    render(<ManufacturingView nodes={[node('abc', 'bom', 'bom.csv')]} onSelect={onSelect} />);
    await userEvent.click(screen.getByRole('button', { name: /bom\.csv/ }));
    expect(onSelect).toHaveBeenCalledWith('abc');
  });

  it('ignores work products that are not part of a fab package', () => {
    // A twin is mostly PRDs, sketches and decisions. None of that belongs
    // in a handoff, and showing it would bury the four things that do.
    render(
      <ManufacturingView
        nodes={[node('1', 'prd', 'requirements.md'), node('2', 'design_sketch', 'concept')]}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.queryByText('requirements.md')).not.toBeInTheDocument();
    expect(screen.getByText(/0 of \d+ outputs produced/)).toBeInTheDocument();
  });

  it('surfaces generic manufacturing files no named output claims', () => {
    render(
      <ManufacturingView
        nodes={[node('1', 'manufacturing_file', 'panel-drawing.pdf')]}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText('Other manufacturing files')).toBeInTheDocument();
    expect(screen.getByText('panel-drawing.pdf')).toBeInTheDocument();
  });
});
