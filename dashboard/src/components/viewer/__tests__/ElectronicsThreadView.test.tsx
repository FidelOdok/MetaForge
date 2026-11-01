import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '../../../test/test-utils';
import type { TwinNode, TwinRelationship } from '../../../types/twin';
import { ElectronicsThreadView, currentOf, isLinked, linkState } from '../ElectronicsThreadView';

vi.mock('../../../api/endpoints/twin', () => ({
  nodeNamedFileUrl: (id: string, path: string) => `/files/${id}/${path}`,
}));

function wp(id: string, wp_type: string, extra: Partial<TwinNode> = {}, props: Record<string, string | number> = {}): TwinNode {
  return {
    id,
    name: id,
    type: 'work_product',
    domain: 'electronics',
    status: 'valid',
    properties: { wp_type, ...props },
    updatedAt: '2026-10-25T10:00:00Z',
    ...extra,
  };
}

const rel = (sourceId: string, targetId: string, type = 'generated_from'): TwinRelationship => ({
  id: `${sourceId}:${targetId}`,
  sourceId,
  targetId,
  type,
  label: type,
});

const files = (previews: string[] = []) => ({ files: [{ path: 'a' }, { path: 'b' }], previews, layers: [], buildOutputs: [], sources: [] });

describe('ElectronicsThreadView (FORGE-687)', () => {
  it('keeps only the highest revision of each item', () => {
    const nodes = [
      wp('sch-1', 'schematic', {}, { item_key: 'SCH-FC', item_revision: 1 }),
      wp('sch-2', 'schematic', {}, { item_key: 'SCH-FC', item_revision: 2 }),
      wp('sch-other', 'schematic'),
      wp('bom', 'bom'),
    ];
    expect(currentOf(nodes, 'schematic').map((n) => n.id)).toEqual(['sch-2', 'sch-other']);
  });

  it('counts a link only for generated_from from the later stage to the earlier', () => {
    const sch = [wp('s', 'schematic')];
    const bom = [wp('b', 'bom')];
    expect(isLinked(sch, bom, [rel('s', 'b')])).toBe(true);
    expect(isLinked(sch, bom, [rel('b', 's')])).toBe(false);
    expect(isLinked(sch, bom, [rel('s', 'b', 'parent_of')])).toBe(false);
  });

  it('draws the thread, its previews, its links and what is missing', () => {
    const onSelect = vi.fn();
    const nodes = [
      wp('bom', 'bom'),
      wp('sch', 'schematic', { designFiles: files(['preview/fc.svg']) }, { item_key: 'SCH-FC', item_revision: 3 }),
      wp('pcb', 'pcb_layout', { designFiles: files() }),
      wp('gbr', 'gerber', { designFiles: files() }),
      wp('fw', 'firmware_source', { designFiles: files() }),
    ];
    render(
      <ElectronicsThreadView
        nodes={nodes}
        relationships={[rel('sch', 'bom'), rel('pcb', 'sch'), rel('gbr', 'pcb')]}
        onSelect={onSelect}
      />,
    );
    expect(screen.getByText('5 of 7 recorded, 3 of 6 links')).toBeInTheDocument();
    const sch = screen.getByTestId('thread-schematic');
    expect(within(sch).getByRole('img')).toHaveAttribute('src', '/files/sch/preview/fc.svg');
    expect(within(sch).getByText('SCH-FC@3 · 2 files')).toBeInTheDocument();
    expect(within(screen.getByTestId('thread-pick_and_place')).getByText('Not recorded')).toBeInTheDocument();
    expect(within(screen.getByTestId('thread-pinmap')).getByText(/kicad.get_pin_mapping/)).toBeInTheDocument();
    expect(screen.getAllByLabelText('generated from')).toHaveLength(3);
    fireEvent.click(within(sch).getByRole('button'));
    expect(onSelect).toHaveBeenCalledWith('sch');
  });

  it('says when both ends are recorded but not linked', () => {
    render(
      <ElectronicsThreadView nodes={[wp('bom', 'bom'), wp('sch', 'schematic')]} relationships={[]} onSelect={() => {}} />,
    );
    expect(screen.getByLabelText('not linked')).toBeInTheDocument();
  });

  it('shows a link to an earlier revision as out of date, not as unlinked', () => {
    const sch1 = wp('sch-1', 'schematic', {}, { item_key: 'SCH-FC', item_revision: 1 });
    const sch2 = wp('sch-2', 'schematic', {}, { item_key: 'SCH-FC', item_revision: 2 });
    const board = wp('pcb', 'pcb_layout');
    const all = [wp('bom', 'bom'), sch1, sch2, board];
    const rels = [rel('pcb', 'sch-1'), rel('sch-2', 'bom')];
    expect(linkState([board], [sch2], all, rels)).toEqual({ state: 'stale', staleFrom: '@1' });
    render(<ElectronicsThreadView nodes={all} relationships={rels} onSelect={() => {}} />);
    expect(screen.getByLabelText('from @1')).toBeInTheDocument();
    expect(screen.getByText(/1 of 6 links\s*, 1 out of date/)).toBeInTheDocument();
  });
});
