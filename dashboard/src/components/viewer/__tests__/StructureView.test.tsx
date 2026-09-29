import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '../../../test/test-utils';

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
