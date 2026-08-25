import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '../../test/test-utils';

vi.mock('../../hooks/use-twin', () => ({
  useTwinNodes: vi.fn(),
  useTwinNode: vi.fn(),
  useTwinRelationships: vi.fn(() => ({ data: [] })),
  useNodeVersionHistory: vi.fn(() => ({ data: [], isLoading: false })),
}));

vi.mock('../../hooks/use-conversion', () => ({
  useUploadAndConvert: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
}));

vi.mock('../../store/viewer-store', () => ({
  useViewerStore: vi.fn((selector) => {
    const state = {
      glbUrl: null,
      manifest: null,
      selectedMeshName: null,
      hiddenMeshes: new Set(),
      explodeFactor: 0,
      viewMode: 'graph',
      loadModel: vi.fn(),
      selectPart: vi.fn(),
      toggleVisibility: vi.fn(),
      setExplodeFactor: vi.fn(),
      setViewMode: vi.fn(),
      reset: vi.fn(),
    };
    return selector(state);
  }),
}));

vi.mock('../../components/viewer/R3FViewer', () => ({
  R3FViewer: () => <div data-testid="r3f-viewer" />,
}));

vi.mock('../../components/viewer/ComponentTree', () => ({
  ComponentTree: () => <div data-testid="component-tree" />,
}));

vi.mock('../../components/viewer/BomAnnotationPanel', () => ({
  BomAnnotationPanel: () => <div data-testid="bom-panel" />,
}));

vi.mock('../../components/viewer/ExplodedViewControls', () => ({
  ExplodedViewControls: () => <div data-testid="exploded-controls" />,
}));

vi.mock('../../components/viewer/TwinGraphCanvas', () => ({
  TwinGraphCanvas: ({ nodes }: { nodes: { name: string }[] }) => (
    <div data-testid="twin-graph-canvas">
      {nodes.map((n) => <span key={n.name}>{n.name}</span>)}
    </div>
  ),
}));

import { TwinViewerPage } from '../TwinViewerPage';
import { useTwinNodes, useTwinNode, useNodeVersionHistory } from '../../hooks/use-twin';
import { fireEvent, act } from '@testing-library/react';
import { useProjectStore } from '../../store/project-store';

const mockUseTwinNodes = vi.mocked(useTwinNodes);
const mockUseTwinNode = vi.mocked(useTwinNode);
const mockUseNodeVersionHistory = vi.mocked(useNodeVersionHistory);

describe('TwinViewerPage', () => {
  it('renders Digital Twin heading', () => {
    mockUseTwinNodes.mockReturnValue({ data: [], isLoading: false, isError: false, refetch: vi.fn() } as unknown as ReturnType<typeof useTwinNodes>);
    mockUseTwinNode.mockReturnValue({ data: undefined, isLoading: false } as ReturnType<typeof useTwinNode>);
    render(<TwinViewerPage />);
    expect(screen.getByText('Digital Twin')).toBeInTheDocument();
  });

  it('shows graph view with empty state by default', () => {
    mockUseTwinNodes.mockReturnValue({ data: [], isLoading: false, isError: false, refetch: vi.fn() } as unknown as ReturnType<typeof useTwinNodes>);
    mockUseTwinNode.mockReturnValue({ data: undefined, isLoading: false } as ReturnType<typeof useTwinNode>);
    render(<TwinViewerPage />);
    expect(screen.getByText('Empty twin')).toBeInTheDocument();
  });

  it('renders node list in graph mode', () => {
    mockUseTwinNodes.mockReturnValue({
      data: [
        { id: 'n1', name: 'bracket-v1.step', type: 'work_product', domain: 'mechanical', status: 'valid', properties: {}, updatedAt: new Date().toISOString() },
      ],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useTwinNodes>);
    mockUseTwinNode.mockReturnValue({ data: undefined, isLoading: false } as ReturnType<typeof useTwinNode>);
    render(<TwinViewerPage />);
    expect(screen.getAllByText('bracket-v1.step').length).toBeGreaterThanOrEqual(1);
  });

  it('shows view mode toggle buttons', () => {
    mockUseTwinNodes.mockReturnValue({ data: [], isLoading: false, isError: false, refetch: vi.fn() } as unknown as ReturnType<typeof useTwinNodes>);
    mockUseTwinNode.mockReturnValue({ data: undefined, isLoading: false } as ReturnType<typeof useTwinNode>);
    render(<TwinViewerPage />);
    // KC spec uses 'MODEL' and 'GRAPH' (uppercase monospace) in the segmented toggle
    expect(screen.getByText('MODEL')).toBeInTheDocument();
    expect(screen.getAllByText('GRAPH').length).toBeGreaterThanOrEqual(1);
  });

  it('shows revision history for a selected node (previously unwired to any UI)', () => {
    const node = {
      id: 'n1',
      name: 'bracket-v1.step',
      type: 'work_product',
      domain: 'mechanical',
      status: 'valid',
      properties: {},
      updatedAt: new Date().toISOString(),
    };
    mockUseTwinNodes.mockReturnValue({
      data: [node],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useTwinNodes>);
    mockUseTwinNode.mockReturnValue({ data: node, isLoading: false } as unknown as ReturnType<typeof useTwinNode>);
    mockUseNodeVersionHistory.mockReturnValue({
      data: [
        { revision: 2, created_at: new Date().toISOString(), content_hash: 'abcdef1234', change_description: 'Widened mounting hole', metadata_snapshot: {} },
        { revision: 1, created_at: new Date().toISOString(), content_hash: '0123456789', change_description: 'Initial import', metadata_snapshot: {} },
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof useNodeVersionHistory>);

    render(<TwinViewerPage />);
    fireEvent.click(screen.getByRole('button', { name: /bracket-v1\.step/ }));

    expect(screen.getByText('History · 2')).toBeInTheDocument();
    expect(screen.getByText('Widened mounting hole')).toBeInTheDocument();
    expect(screen.getByText('Initial import')).toBeInTheDocument();
  });

  it('does not render a history section when a node has no revisions', () => {
    const node = {
      id: 'n1',
      name: 'bracket-v1.step',
      type: 'work_product',
      domain: 'mechanical',
      status: 'valid',
      properties: {},
      updatedAt: new Date().toISOString(),
    };
    mockUseTwinNodes.mockReturnValue({
      data: [node],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useTwinNodes>);
    mockUseTwinNode.mockReturnValue({ data: node, isLoading: false } as unknown as ReturnType<typeof useTwinNode>);
    mockUseNodeVersionHistory.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useNodeVersionHistory>);

    render(<TwinViewerPage />);
    fireEvent.click(screen.getByRole('button', { name: /bracket-v1\.step/ }));

    expect(screen.queryByText(/History ·/)).not.toBeInTheDocument();
  });

  it('clears the selected node (detail panel + breadcrumb) when the active project changes', () => {
    // Regression (MET-674): switching the active project re-fetched the node
    // list/canvas for the new project, but left `selectedId` -- and so the
    // detail panel and the "Digital Twin > {name}" breadcrumb -- pointing at
    // the PREVIOUS project's node indefinitely.
    const node = {
      id: 'n1',
      name: 'bracket-v1.step',
      type: 'work_product',
      domain: 'mechanical',
      status: 'valid',
      properties: {},
      updatedAt: new Date().toISOString(),
    };
    mockUseTwinNodes.mockReturnValue({
      data: [node],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useTwinNodes>);
    // Reactive mock: only "selected" (a truthy id) resolves to the node, so
    // clearing selectedId is actually observable in this test.
    mockUseTwinNode.mockImplementation(
      (id?: string) =>
        ({ data: id ? node : undefined, isLoading: false }) as unknown as ReturnType<typeof useTwinNode>,
    );
    mockUseNodeVersionHistory.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useNodeVersionHistory>);

    useProjectStore.setState({ activeProjectId: 'proj-a', hasSelected: true });
    render(<TwinViewerPage />);

    fireEvent.click(screen.getByRole('button', { name: /bracket-v1\.step/ }));
    // Selected: the node's name renders in the breadcrumb/detail panel on
    // top of whatever static places it always renders (e.g. the list row and
    // the scene dropdown's <option>).
    const selectedCount = screen.getAllByText('bracket-v1.step').length;
    expect(selectedCount).toBeGreaterThan(1);

    act(() => {
      useProjectStore.setState({ activeProjectId: 'proj-b', hasSelected: true });
    });

    // The breadcrumb/detail panel occurrences (derived from selectedId)
    // disappear -- fewer occurrences than while a node was selected, even
    // though the statically-mocked list/dropdown still render the name.
    expect(screen.getAllByText('bracket-v1.step').length).toBeLessThan(selectedCount);
  });
});
