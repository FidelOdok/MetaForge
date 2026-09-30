import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '../../../test/test-utils';

vi.mock('../../../hooks/use-hierarchy', () => ({
  useHierarchyTree: vi.fn(),
}));

import { StructureView } from '../StructureView';
import { useHierarchyTree } from '../../../hooks/use-hierarchy';
import type { HierarchyNode } from '../../../types/hierarchy';

const mockUseHierarchyTree = vi.mocked(useHierarchyTree);

function node(overrides: Partial<HierarchyNode>): HierarchyNode {
  return {
    id: 'n1',
    name: 'upper_arm',
    kind: 'subsystem',
    parentId: null,
    quantity: null,
    placement: null,
    massKg: 0,
    cost: 0,
    massBudgetKg: null,
    massOverBudget: null,
    costBudget: null,
    costOverBudget: null,
    massBudgetOwner: null,
    massBudgetDiscipline: null,
    costBudgetOwner: null,
    costBudgetDiscipline: null,
    interfaces: [],
    realizedByWorkProductId: null,
    instanceOfBomItemId: null,
    ...overrides,
  };
}

describe('StructureView', () => {
  it('shows empty state with no project selected', () => {
    mockUseHierarchyTree.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<
      typeof useHierarchyTree
    >);
    render(<StructureView projectId={null} onSelect={() => {}} />);
    expect(screen.getByText('No project selected')).toBeInTheDocument();
  });

  it('shows empty hierarchy state', () => {
    mockUseHierarchyTree.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<
      typeof useHierarchyTree
    >);
    render(<StructureView projectId="p1" onSelect={() => {}} />);
    expect(screen.getByText('No hierarchy yet')).toBeInTheDocument();
  });

  it('renders an interfaces badge when a node has interfaces', () => {
    mockUseHierarchyTree.mockReturnValue({
      data: [
        node({
          id: 'n1',
          name: 'upper_arm',
          interfaces: [
            {
              otherComponent: 'shoulder',
              interfaceType: 'mechanical',
              description: 'joint',
              quantities: [{ metric: 'tip_deflection', unit: 'mm', limit: 0.5, op: '<=' }],
            },
          ],
        }),
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof useHierarchyTree>);
    render(<StructureView projectId="p1" onSelect={() => {}} />);
    expect(screen.getByLabelText('1 interface')).toBeInTheDocument();
  });

  it('does not render an interfaces badge when a node has none', () => {
    mockUseHierarchyTree.mockReturnValue({
      data: [node({ id: 'n1', name: 'upper_arm', interfaces: [] })],
      isLoading: false,
    } as unknown as ReturnType<typeof useHierarchyTree>);
    render(<StructureView projectId="p1" onSelect={() => {}} />);
    expect(screen.queryByLabelText(/interface/)).not.toBeInTheDocument();
  });

  it('shows a DFM check button only for a node with real geometry', () => {
    mockUseHierarchyTree.mockReturnValue({
      data: [
        node({ id: 'n1', name: 'realized', realizedByWorkProductId: 'wp-1' }),
        node({ id: 'n2', name: 'placeholder' }),
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof useHierarchyTree>);
    render(<StructureView projectId="p1" onSelect={() => {}} />);
    expect(screen.getByTestId('dfm-check-button-n1')).toBeInTheDocument();
    expect(screen.queryByTestId('dfm-check-button-n2')).not.toBeInTheDocument();
  });

  it('opens the DFM overhang panel with a disabled run button until a mesh file is entered', () => {
    mockUseHierarchyTree.mockReturnValue({
      data: [node({ id: 'n1', name: 'upper_arm', realizedByWorkProductId: 'wp-1' })],
      isLoading: false,
    } as unknown as ReturnType<typeof useHierarchyTree>);
    render(<StructureView projectId="p1" onSelect={() => {}} />);

    fireEvent.click(screen.getByTestId('dfm-check-button-n1'));
    expect(screen.getByTestId('dfm-overhang-panel')).toBeInTheDocument();
    const runButton = screen.getByTestId('run-dfm-check');
    expect(runButton).toBeDisabled();

    fireEvent.change(screen.getByTestId('dfm-mesh-file-input'), {
      target: { value: '/workspace/upper_arm.inp' },
    });
    expect(runButton).not.toBeDisabled();
  });

  it('shows a Release for manufacture button only for a node with real geometry', () => {
    mockUseHierarchyTree.mockReturnValue({
      data: [
        node({ id: 'n1', name: 'realized', realizedByWorkProductId: 'wp-1' }),
        node({ id: 'n2', name: 'placeholder' }),
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof useHierarchyTree>);
    render(<StructureView projectId="p1" onSelect={() => {}} />);
    expect(screen.getByTestId('manufacture-release-button-n1')).toBeInTheDocument();
    expect(screen.queryByTestId('manufacture-release-button-n2')).not.toBeInTheDocument();
  });

  it('opens the manufacture release panel with a process selector defaulting to 3D print', () => {
    mockUseHierarchyTree.mockReturnValue({
      data: [node({ id: 'n1', name: 'upper_arm', realizedByWorkProductId: 'wp-1' })],
      isLoading: false,
    } as unknown as ReturnType<typeof useHierarchyTree>);
    render(<StructureView projectId="p1" onSelect={() => {}} />);

    fireEvent.click(screen.getByTestId('manufacture-release-button-n1'));
    expect(screen.getByTestId('manufacture-release-panel')).toBeInTheDocument();
    const select = screen.getByTestId('manufacture-process-select') as HTMLSelectElement;
    expect(select.value).toBe('3d_print');

    fireEvent.change(select, { target: { value: 'cnc' } });
    expect(select.value).toBe('cnc');
    expect(screen.getByTestId('run-manufacture-release')).not.toBeDisabled();
  });

  it('shows a Bring-up checklist button only for a node with real geometry', () => {
    mockUseHierarchyTree.mockReturnValue({
      data: [
        node({ id: 'n1', name: 'realized', realizedByWorkProductId: 'wp-1' }),
        node({ id: 'n2', name: 'placeholder' }),
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof useHierarchyTree>);
    render(<StructureView projectId="p1" onSelect={() => {}} />);
    expect(screen.getByTestId('bringup-checklist-button-n1')).toBeInTheDocument();
    expect(screen.queryByTestId('bringup-checklist-button-n2')).not.toBeInTheDocument();
  });

  it('opens the bring-up checklist panel with an enabled generate button', () => {
    mockUseHierarchyTree.mockReturnValue({
      data: [node({ id: 'n1', name: 'upper_arm', realizedByWorkProductId: 'wp-1' })],
      isLoading: false,
    } as unknown as ReturnType<typeof useHierarchyTree>);
    render(<StructureView projectId="p1" onSelect={() => {}} />);

    fireEvent.click(screen.getByTestId('bringup-checklist-button-n1'));
    expect(screen.getByTestId('bringup-checklist-panel')).toBeInTheDocument();
    expect(screen.getByTestId('run-bringup-checklist')).not.toBeDisabled();
  });

  it('shows the allocation owner as a title on the mass cell', () => {
    mockUseHierarchyTree.mockReturnValue({
      data: [
        node({
          id: 'n1',
          name: 'upper_arm',
          massKg: 2.0,
          massBudgetKg: 4.5,
          massOverBudget: false,
          massBudgetOwner: 'alice',
          massBudgetDiscipline: 'mechanical',
        }),
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof useHierarchyTree>);
    render(<StructureView projectId="p1" onSelect={() => {}} />);
    expect(screen.getByTitle('Owner: alice (mechanical)')).toBeInTheDocument();
  });
});
