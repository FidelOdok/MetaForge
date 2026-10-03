import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '../../../test/test-utils';
import { AssemblyPartTree, partDimensions } from '../AssemblyPartTree';
import type { AssemblyPart } from '../../../types/twin';

const selectPart = vi.fn();
let selected: string | null = null;

vi.mock('../../../store/viewer-store', () => ({
  useViewerStore: vi.fn((selector) =>
    selector({
      manifest: { parts: [{ name: 'Shelf Board', meshName: 'mesh_7', children: [] }] },
      selectedMeshName: selected,
      selectPart,
    }),
  ),
}));

const parts: AssemblyPart[] = [
  {
    node_id: 'n1',
    name: 'Shelf Board',
    material: 'Birch plywood',
    position_bbox_mm: { min: [0, 0, 0], max: [300, 120, 12] },
  },
  { node_id: 'n2', name: 'Left Bracket', position_bbox_mm: null },
];

describe('AssemblyPartTree', () => {
  beforeEach(() => {
    selectPart.mockClear();
    selected = null;
  });

  it('lists each part with material and dimensions', () => {
    render(<AssemblyPartTree parts={parts} />);
    expect(screen.getByText('Shelf Board')).toBeInTheDocument();
    expect(screen.getByText('Birch plywood')).toBeInTheDocument();
    expect(screen.getByText('300 x 120 x 12 mm')).toBeInTheDocument();
    expect(screen.getByText('Material unspecified')).toBeInTheDocument();
  });

  it('selecting a part selects its mesh in the viewer', () => {
    render(<AssemblyPartTree parts={parts} />);
    fireEvent.click(screen.getByRole('treeitem', { name: /Shelf Board/ }));
    expect(selectPart).toHaveBeenCalledWith('mesh_7');
  });

  it('highlights the selected part', () => {
    selected = 'mesh_7';
    render(<AssemblyPartTree parts={parts} />);
    expect(screen.getByRole('treeitem', { name: /Shelf Board/ })).toHaveAttribute('aria-selected', 'true');
  });

  it('partDimensions is null without a box', () => {
    expect(partDimensions(parts[1]!)).toBeNull();
  });
});
