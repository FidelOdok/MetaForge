import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '../../../test/test-utils';

vi.mock('../../../hooks/use-hierarchy', () => ({
  useHierarchyTree: vi.fn(),
}));
vi.mock('../../../hooks/use-harness-estimate', () => ({
  useHarnessEstimate: vi.fn(),
}));
vi.mock('../../../hooks/use-technical-drawing', () => ({
  useTechnicalDrawings: vi.fn(),
  useApproveTechnicalDrawing: vi.fn(),
}));

import { StructureView } from '../StructureView';
import { useHierarchyTree } from '../../../hooks/use-hierarchy';
import { useHarnessEstimate } from '../../../hooks/use-harness-estimate';
import {
  useApproveTechnicalDrawing,
  useTechnicalDrawings,
} from '../../../hooks/use-technical-drawing';
import type { HierarchyNode } from '../../../types/hierarchy';

const mockUseHierarchyTree = vi.mocked(useHierarchyTree);
const mockUseHarnessEstimate = vi.mocked(useHarnessEstimate);
const mockUseTechnicalDrawings = vi.mocked(useTechnicalDrawings);
const mockUseApproveTechnicalDrawing = vi.mocked(useApproveTechnicalDrawing);
// Safe default for every test that doesn't open the harness-estimate panel.
mockUseHarnessEstimate.mockReturnValue({
  data: undefined,
  isLoading: false,
  isError: false,
} as unknown as ReturnType<typeof useHarnessEstimate>);
// Safe default for every test that doesn't open the technical-drawing panel.
mockUseTechnicalDrawings.mockReturnValue({
  data: undefined,
  isLoading: false,
  isError: false,
} as unknown as ReturnType<typeof useTechnicalDrawings>);
mockUseApproveTechnicalDrawing.mockReturnValue({
  mutate: vi.fn(),
  isPending: false,
} as unknown as ReturnType<typeof useApproveTechnicalDrawing>);

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
    drawPeakW: 0,
    drawAverageW: 0,
    outputW: 0,
    dissipationW: 0,
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

  it('shows a Check interference button only for a node with real geometry', () => {
    mockUseHierarchyTree.mockReturnValue({
      data: [
        node({ id: 'n1', name: 'realized', realizedByWorkProductId: 'wp-1' }),
        node({ id: 'n2', name: 'placeholder' }),
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof useHierarchyTree>);
    render(<StructureView projectId="p1" onSelect={() => {}} />);
    expect(screen.getByTestId('interference-check-button-n1')).toBeInTheDocument();
    expect(screen.queryByTestId('interference-check-button-n2')).not.toBeInTheDocument();
  });

  it('opens the interference check panel with a disabled run button until a second part id is entered', () => {
    mockUseHierarchyTree.mockReturnValue({
      data: [node({ id: 'n1', name: 'upper_arm', realizedByWorkProductId: 'wp-1' })],
      isLoading: false,
    } as unknown as ReturnType<typeof useHierarchyTree>);
    render(<StructureView projectId="p1" onSelect={() => {}} />);

    fireEvent.click(screen.getByTestId('interference-check-button-n1'));
    expect(screen.getByTestId('interference-check-panel')).toBeInTheDocument();
    const runButton = screen.getByTestId('run-interference-check');
    expect(runButton).toBeDisabled();

    fireEvent.change(screen.getByTestId('interference-other-part-input'), {
      target: { value: 'wp-2' },
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

  it('shows a Firmware scaffold button only for a node with real geometry', () => {
    mockUseHierarchyTree.mockReturnValue({
      data: [
        node({ id: 'n1', name: 'realized', realizedByWorkProductId: 'wp-1' }),
        node({ id: 'n2', name: 'placeholder' }),
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof useHierarchyTree>);
    render(<StructureView projectId="p1" onSelect={() => {}} />);
    expect(screen.getByTestId('firmware-scaffold-button-n1')).toBeInTheDocument();
    expect(screen.queryByTestId('firmware-scaffold-button-n2')).not.toBeInTheDocument();
  });

  it('opens the firmware scaffold panel with an enabled generate button', () => {
    mockUseHierarchyTree.mockReturnValue({
      data: [node({ id: 'n1', name: 'upper_arm', realizedByWorkProductId: 'wp-1' })],
      isLoading: false,
    } as unknown as ReturnType<typeof useHierarchyTree>);
    render(<StructureView projectId="p1" onSelect={() => {}} />);

    fireEvent.click(screen.getByTestId('firmware-scaffold-button-n1'));
    expect(screen.getByTestId('firmware-scaffold-panel')).toBeInTheDocument();
    expect(screen.getByTestId('run-firmware-scaffold')).not.toBeDisabled();
  });

  it('shows a Harness estimate button only for a node with real geometry', () => {
    mockUseHierarchyTree.mockReturnValue({
      data: [
        node({ id: 'n1', name: 'realized', realizedByWorkProductId: 'wp-1' }),
        node({ id: 'n2', name: 'placeholder' }),
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof useHierarchyTree>);
    render(<StructureView projectId="p1" onSelect={() => {}} />);
    expect(screen.getByTestId('harness-estimate-button-n1')).toBeInTheDocument();
    expect(screen.queryByTestId('harness-estimate-button-n2')).not.toBeInTheDocument();
  });

  it('opens the harness estimate panel and shows the real joint table', () => {
    mockUseHierarchyTree.mockReturnValue({
      data: [node({ id: 'n1', name: 'upper_arm', realizedByWorkProductId: 'wp-1' })],
      isLoading: false,
    } as unknown as ReturnType<typeof useHierarchyTree>);
    mockUseHarnessEstimate.mockReturnValue({
      data: {
        workProductId: 'wp-1',
        joints: [
          {
            stepNumber: 1,
            jointName: 'joint_1',
            jointType: 'revolute',
            base: 'base_link',
            follower: 'link_1',
            segmentLengthMm: 5,
            cableLengthEstimateMm: 5,
          },
        ],
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useHarnessEstimate>);
    render(<StructureView projectId="p1" onSelect={() => {}} />);

    fireEvent.click(screen.getByTestId('harness-estimate-button-n1'));
    expect(screen.getByTestId('harness-estimate-panel')).toBeInTheDocument();
    expect(screen.getByTestId('harness-estimate-joints')).toHaveTextContent('joint_1');
    expect(screen.getByTestId('harness-estimate-joints')).toHaveTextContent('5.0');
  });

  it('shows a Technical drawing button only for a node with real geometry', () => {
    mockUseHierarchyTree.mockReturnValue({
      data: [
        node({ id: 'n1', name: 'realized', realizedByWorkProductId: 'wp-1' }),
        node({ id: 'n2', name: 'placeholder' }),
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof useHierarchyTree>);
    render(<StructureView projectId="p1" onSelect={() => {}} />);
    expect(screen.getByTestId('technical-drawing-button-n1')).toBeInTheDocument();
    expect(screen.queryByTestId('technical-drawing-button-n2')).not.toBeInTheDocument();
  });

  it('opens the technical drawing panel and shows the real structured data', () => {
    mockUseHierarchyTree.mockReturnValue({
      data: [node({ id: 'n1', name: 'upper_arm', realizedByWorkProductId: 'wp-1' })],
      isLoading: false,
    } as unknown as ReturnType<typeof useHierarchyTree>);
    mockUseTechnicalDrawings.mockReturnValue({
      data: [
        {
          nodeId: 'drawing-1',
          createdAt: '2026-10-01T00:00:00Z',
          name: 'Elbow Bracket Drawing',
          partName: 'Elbow Bracket',
          dimensions: [
            { feature: 'bore_diameter', nominalMm: 10, tolerancePlusMm: 0.02, toleranceMinusMm: 0 },
          ],
          gdtCallouts: [
            { feature: 'mounting_face', symbol: 'flatness', toleranceValueMm: 0.05, datumRefs: [] },
          ],
          surfaceFinishes: [{ feature: 'bore_surface', raUm: 1.6 }],
          inspectionRequirements: ['CMM bore diameter check'],
          approved: false,
          approvedAt: null,
          approvedBy: null,
        },
      ],
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useTechnicalDrawings>);
    render(<StructureView projectId="p1" onSelect={() => {}} />);

    fireEvent.click(screen.getByTestId('technical-drawing-button-n1'));
    expect(screen.getByTestId('technical-drawing-panel')).toBeInTheDocument();
    const content = screen.getByTestId('technical-drawing-content');
    expect(content).toHaveTextContent('bore_diameter');
    expect(content).toHaveTextContent('flatness');
    expect(content).toHaveTextContent('bore_surface');
    expect(content).toHaveTextContent('CMM bore diameter check');
    expect(screen.getByTestId('approve-technical-drawing')).toBeInTheDocument();
  });

  it('hides the approve button once a drawing is already approved', () => {
    mockUseHierarchyTree.mockReturnValue({
      data: [node({ id: 'n1', name: 'upper_arm', realizedByWorkProductId: 'wp-1' })],
      isLoading: false,
    } as unknown as ReturnType<typeof useHierarchyTree>);
    mockUseTechnicalDrawings.mockReturnValue({
      data: [
        {
          nodeId: 'drawing-1',
          createdAt: '2026-10-01T00:00:00Z',
          name: 'Elbow Bracket Drawing',
          partName: 'Elbow Bracket',
          dimensions: [],
          gdtCallouts: [],
          surfaceFinishes: [],
          inspectionRequirements: [],
          approved: true,
          approvedAt: '2026-10-01T01:00:00Z',
          approvedBy: 'fidel',
        },
      ],
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useTechnicalDrawings>);
    render(<StructureView projectId="p1" onSelect={() => {}} />);

    fireEvent.click(screen.getByTestId('technical-drawing-button-n1'));
    expect(screen.getByTestId('technical-drawing-content')).toHaveTextContent('Approved by fidel');
    expect(screen.queryByTestId('approve-technical-drawing')).not.toBeInTheDocument();
  });

  it('shows the node power draw on the Structure row', () => {
    mockUseHierarchyTree.mockReturnValue({
      data: [node({ id: 'n1', name: 'actuator', drawAverageW: 165.6, drawPeakW: 331.2 })],
      isLoading: false,
    } as unknown as ReturnType<typeof useHierarchyTree>);
    render(<StructureView projectId="p1" onSelect={() => {}} />);
    expect(screen.getByText('165.6W')).toBeInTheDocument();
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
