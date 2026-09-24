import { describe, it, expect, vi, beforeEach } from 'vitest';
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

const mockUrdfMutate = vi.fn();
const mockSdfMutate = vi.fn();
const mockUsdMutate = vi.fn();
vi.mock('../../hooks/use-cad-export', () => ({
  useExportUrdf: () => ({ mutate: mockUrdfMutate, isPending: false }),
  useExportSdf: () => ({ mutate: mockSdfMutate, isPending: false }),
  useExportUsd: () => ({ mutate: mockUsdMutate, isPending: false }),
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

vi.mock('../../components/viewer/TwinAgentChat', () => ({
  TwinAgentChat: ({ projectId }: { projectId: string | null }) => (
    <div data-testid="twin-agent-chat">{projectId ?? 'unscoped'}</div>
  ),
}));

vi.mock('../../components/viewer/TwinGraphCanvas', () => ({
  TwinGraphCanvas: ({ nodes }: { nodes: { name: string }[] }) => (
    <div data-testid="twin-graph-canvas">
      {nodes.map((n) => <span key={n.name}>{n.name}</span>)}
    </div>
  ),
}));

import { TwinViewerPage } from '../TwinViewerPage';
import { useTwinNodes, useTwinNode, useTwinRelationships, useNodeVersionHistory } from '../../hooks/use-twin';
import { fireEvent, act, within } from '@testing-library/react';
import { useNavigate } from 'react-router-dom';
import { useProjectStore } from '../../store/project-store';
import { useLayoutStore } from '../../store/layout-store';
import { setSampleModeForTests } from '../../lib/sample-workspace';

const mockUseTwinNodes = vi.mocked(useTwinNodes);
const mockUseTwinNode = vi.mocked(useTwinNode);
const mockUseTwinRelationships = vi.mocked(useTwinRelationships);
const mockUseNodeVersionHistory = vi.mocked(useNodeVersionHistory);

// MET-686: a harness for simulating an in-SPA navigation that changes the
// ?node= query string on the SAME /twin path -- react-router does not
// remount TwinViewerPage for a query-only change (no :param in the route),
// so a raw `window.history.pushState` (which react-router's BrowserRouter
// instance never observes) can't reproduce it; a real `navigate()` call can.
function TwinWithNavHarness() {
  const navigate = useNavigate();
  return (
    <>
      <button onClick={() => navigate('/twin')}>goto-twin-no-query</button>
      <TwinViewerPage />
    </>
  );
}

describe('TwinViewerPage', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/twin');
    // Expanded explorer so node rows render as buttons (collapsed shows the domain rail).
    useLayoutStore.setState({ sidebarCollapsed: false });
  });


  it('renders Digital Twin heading', () => {
    mockUseTwinNodes.mockReturnValue({ data: [], isLoading: false, isError: false, refetch: vi.fn() } as unknown as ReturnType<typeof useTwinNodes>);
    mockUseTwinNode.mockReturnValue({ data: undefined, isLoading: false } as ReturnType<typeof useTwinNode>);
    render(<TwinViewerPage />);
    expect(screen.getByText('Digital Twin')).toBeInTheDocument();
  });

  it('scopes relationships to the active project (MET-677)', () => {
    useProjectStore.setState({ activeProjectId: 'proj-active', hasSelected: true });
    mockUseTwinNodes.mockReturnValue({ data: [], isLoading: false, isError: false, refetch: vi.fn() } as unknown as ReturnType<typeof useTwinNodes>);
    mockUseTwinNode.mockReturnValue({ data: undefined, isLoading: false } as ReturnType<typeof useTwinNode>);
    render(<TwinViewerPage />);
    expect(mockUseTwinRelationships).toHaveBeenCalledWith('proj-active');
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
    const group = screen.getByRole('group', { name: 'View mode' });
    for (const label of ['Graph', 'Model', 'Sim', 'Assembly']) {
      expect(within(group).getByRole('button', { name: label })).toBeInTheDocument();
    }
    expect(within(group).getByRole('button', { name: 'Graph' })).toHaveAttribute('aria-pressed', 'true');
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

  it('clears the selected node when the ?node= query param disappears on the same /twin route (MET-686)', () => {
    // Regression: the Sidebar's "Digital Twin" nav item links to the bare
    // /twin (no query). Since /twin has no :param, react-router re-renders
    // TwinViewerPage in place rather than remounting it, so the deep-link
    // effect must actively clear selectedId when the param is gone -- it
    // used to only ever set it, never clear it, leaving a stale selection.
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
    mockUseTwinNode.mockImplementation(
      (id?: string) =>
        ({ data: id ? node : undefined, isLoading: false }) as unknown as ReturnType<typeof useTwinNode>,
    );
    mockUseNodeVersionHistory.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useNodeVersionHistory>);
    useProjectStore.setState({ activeProjectId: 'proj-a', hasSelected: true });

    window.history.pushState({}, '', '/twin?node=n1');
    render(<TwinWithNavHarness />);

    const selectedCount = screen.getAllByText('bracket-v1.step').length;
    expect(selectedCount).toBeGreaterThan(1);

    fireEvent.click(screen.getByRole('button', { name: 'goto-twin-no-query' }));

    expect(screen.getAllByText('bracket-v1.step').length).toBeLessThan(selectedCount);
  });

  it('does not clear a deep-linked node when the active project auto-selects shortly after mount (MET-686)', () => {
    // Regression: on a cold session (nothing persisted, hasSelected=false),
    // useActiveProject's own "auto-select the newest project" effect can
    // resolve a moment after mount and change activeProjectId from null to
    // some project -- indistinguishable, at the naive project-change-clears-
    // selection effect, from a real project switch. That wiped out the node
    // the ?node= deep link had *just* selected.
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
    mockUseTwinNode.mockImplementation(
      (id?: string) =>
        ({ data: id ? node : undefined, isLoading: false }) as unknown as ReturnType<typeof useTwinNode>,
    );
    mockUseNodeVersionHistory.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useNodeVersionHistory>);
    useProjectStore.setState({ activeProjectId: null, hasSelected: false });

    window.history.pushState({}, '', '/twin?node=n1');
    render(<TwinViewerPage />);

    const selectedCount = screen.getAllByText('bracket-v1.step').length;
    expect(selectedCount).toBeGreaterThan(1);

    // The cold-start auto-select landing (null -> a project) must not clear it.
    act(() => {
      useProjectStore.setState({ activeProjectId: 'proj-auto', hasSelected: true });
    });
    expect(screen.getAllByText('bracket-v1.step').length).toBe(selectedCount);

    // A genuine subsequent switch away from an already-active project still must.
    act(() => {
      useProjectStore.setState({ activeProjectId: 'proj-other', hasSelected: true });
    });
    expect(screen.getAllByText('bracket-v1.step').length).toBeLessThan(selectedCount);
  });

  describe('export for robotics sim (MET-720)', () => {
    const cadNode = {
      id: 'n1',
      name: 'bracket-v1.step',
      type: 'work_product',
      domain: 'mechanical',
      status: 'valid',
      properties: { wp_type: 'cad_model' },
      updatedAt: new Date().toISOString(),
    };

    beforeEach(() => {
      mockUseTwinNodes.mockReturnValue({
        data: [cadNode],
        isLoading: false,
        isError: false,
        refetch: vi.fn(),
      } as unknown as ReturnType<typeof useTwinNodes>);
      mockUseTwinNode.mockReturnValue({ data: cadNode, isLoading: false } as unknown as ReturnType<typeof useTwinNode>);
      mockUseNodeVersionHistory.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useNodeVersionHistory>);
      mockUrdfMutate.mockClear();
      mockSdfMutate.mockClear();
      mockUsdMutate.mockClear();
    });

    it('toggles the export panel and submits a URDF export with the entered params', () => {
      render(<TwinViewerPage />);
      fireEvent.click(screen.getByRole('button', { name: /bracket-v1\.step/ }));

      fireEvent.click(screen.getByTitle('Export for robotics sim (URDF/SDF/USD)'));
      expect(screen.getByText('Export for robotics sim')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: 'Export URDF' }));

      expect(mockUrdfMutate).toHaveBeenCalledWith(
        expect.objectContaining({ node_id: 'n1', link_name: 'base_link', xacro: false }),
        expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }),
      );
    });

    it('switching format tabs submits to the matching mutation', () => {
      render(<TwinViewerPage />);
      fireEvent.click(screen.getByRole('button', { name: /bracket-v1\.step/ }));
      fireEvent.click(screen.getByTitle('Export for robotics sim (URDF/SDF/USD)'));

      fireEvent.click(screen.getByRole('button', { name: 'sdf' }));
      fireEvent.click(screen.getByRole('button', { name: 'Export SDF' }));

      // link_name is a shared field across tabs (an edit shouldn't be
      // clobbered by switching format) -- still whatever it defaulted to.
      expect(mockSdfMutate).toHaveBeenCalledWith(
        expect.objectContaining({ node_id: 'n1', model_name: 'model', link_name: 'base_link' }),
        expect.anything(),
      );
      expect(mockUrdfMutate).not.toHaveBeenCalled();
    });

    it('shows download links (prefixed for the /api proxy) after a successful export', () => {
      render(<TwinViewerPage />);
      fireEvent.click(screen.getByRole('button', { name: /bracket-v1\.step/ }));
      fireEvent.click(screen.getByTitle('Export for robotics sim (URDF/SDF/USD)'));
      fireEvent.click(screen.getByRole('button', { name: 'Export URDF' }));

      const onSuccess = mockUrdfMutate.mock.calls[0]?.[1].onSuccess as (data: unknown) => void;
      act(() => {
        onSuccess({
          output_file: { filename: 'model.urdf', download_url: '/v1/cad-export/download/abc/model.urdf' },
          mesh_file: { filename: 'model.stl', download_url: '/v1/cad-export/download/abc/model.stl' },
        });
      });

      const urdfLink = screen.getByText('model.urdf').closest('a');
      expect(urdfLink).toHaveAttribute('href', '/api/v1/cad-export/download/abc/model.urdf');
      const meshLink = screen.getByText('model.stl').closest('a');
      expect(meshLink).toHaveAttribute('href', '/api/v1/cad-export/download/abc/model.stl');
    });
  });

  describe('console workspace (status strip, explorer, inspector)', () => {
    const nodes = [
      { id: 'n1', name: 'bracket-v1.step', type: 'work_product', domain: 'mechanical', status: 'valid', properties: { wp_type: 'cad_model' }, updatedAt: '2026-09-22T12:00:00Z' },
      { id: 'c1', name: 'Clearance', type: 'constraint', domain: 'mechanical', status: 'violation', properties: {}, updatedAt: '2026-09-22T11:00:00Z' },
      { id: 'e1', name: 'power-budget', type: 'work_product', domain: 'electronics', status: 'stale', properties: {}, updatedAt: '2026-09-22T10:00:00Z' },
    ];

    beforeEach(() => {
      mockUseTwinNodes.mockReturnValue({ data: nodes, isLoading: false, isError: false, refetch: vi.fn() } as unknown as ReturnType<typeof useTwinNodes>);
      mockUseTwinRelationships.mockReturnValue({
        data: [{ id: 'r1', sourceId: 'n1', targetId: 'c1', type: 'constrained_by', label: 'constrained by' }],
      } as unknown as ReturnType<typeof useTwinRelationships>);
      mockUseTwinNode.mockImplementation(
        (id?: string) => ({ data: nodes.find((n) => n.id === id), isLoading: false }) as unknown as ReturnType<typeof useTwinNode>,
      );
      mockUseNodeVersionHistory.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useNodeVersionHistory>);
    });

    it('summarises attention, totals and orphan nodes, and links to a design run', () => {
      useProjectStore.setState({ activeProjectId: 'proj-a', hasSelected: true });
      render(<TwinViewerPage />);
      const strip = screen.getByRole('region', { name: 'Needs attention' });
      expect(within(strip).getByRole('button', { name: /2 need attention/ })).toBeInTheDocument();
      expect(within(strip).getByRole('button', { name: /3 work products & nodes/ })).toBeInTheDocument();
      expect(within(strip).getByText('without relationships').parentElement).toHaveTextContent('1 without relationships');
      expect(within(strip).getByRole('link', { name: /Start design run/ })).toHaveAttribute('href', '/runs/new?project=proj-a');
      expect(screen.queryByText('Sample data · resets on refresh')).not.toBeInTheDocument();
    });

    it('filters the explorer to nodes needing attention and by search', () => {
      render(<TwinViewerPage />);
      const explorer = screen.getByRole('complementary', { name: 'Explorer' });
      fireEvent.click(within(explorer).getByRole('button', { name: 'Needs attention' }));
      expect(within(explorer).queryByRole('button', { name: /bracket-v1\.step/ })).not.toBeInTheDocument();
      expect(within(explorer).getByRole('button', { name: /Clearance/ })).toBeInTheDocument();
      fireEvent.click(within(explorer).getByRole('button', { name: 'All' }));
      fireEvent.change(screen.getByRole('searchbox', { name: 'Search the twin' }), { target: { value: 'power' } });
      expect(within(explorer).getByText('1 nodes')).toBeInTheDocument();
      expect(within(explorer).getByRole('button', { name: /power-budget/ })).toBeInTheDocument();
    });

    it('shows the domain rail when the explorer is minimised', () => {
      useLayoutStore.setState({ sidebarCollapsed: true });
      render(<TwinViewerPage />);
      const explorer = screen.getByRole('complementary', { name: 'Explorer' });
      expect(within(explorer).getByTitle('All work products')).toHaveTextContent('3');
      expect(within(explorer).getByTitle('mechanical')).toHaveTextContent('ME2');
      expect(within(explorer).getByTitle('electronics')).toHaveTextContent('EL1');
    });

    it('inspector lists linked constraints for the selected node', () => {
      render(<TwinViewerPage />);
      fireEvent.click(screen.getByRole('button', { name: /bracket-v1\.step/ }));
      const inspector = screen.getByRole('complementary', { name: 'Node inspector' });
      expect(within(inspector).getByText('mechanical · work_product')).toBeInTheDocument();
      fireEvent.click(within(inspector).getByRole('button', { name: 'constraints' }));
      expect(within(inspector).getByRole('button', { name: /Clearance/ })).toHaveTextContent('violation');
    });

    it('opens the assembly view and the timeline', () => {
      render(<TwinViewerPage />);
      fireEvent.click(screen.getByRole('button', { name: 'Assembly' }));
      expect(screen.getByText('Build the assembly')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Timeline' }));
      expect(screen.getByText('Latest work product updates')).toBeInTheDocument();
    });

    it('shows the gateway-unavailable state when the twin query fails', () => {
      mockUseTwinNodes.mockReturnValue({ data: undefined, isLoading: false, isError: true, refetch: vi.fn() } as unknown as ReturnType<typeof useTwinNodes>);
      render(<TwinViewerPage />);
      expect(screen.getByText('Twin data unavailable')).toBeInTheDocument();
      expect(screen.getByText('Gateway disconnected')).toBeInTheDocument();
    });

    it('marks sample mode with a badge and pins the sample project', () => {
      setSampleModeForTests(true);
      try {
        render(<TwinViewerPage />);
        expect(screen.getByText('Sample data · resets on refresh')).toBeInTheDocument();
        expect(screen.getByText('Drone FC · sample')).toBeInTheDocument();
        expect(mockUseTwinNodes).toHaveBeenLastCalledWith('sample-drone-fc');
        expect(screen.getByTestId('twin-agent-chat')).toHaveTextContent('sample-drone-fc');
      } finally {
        setSampleModeForTests(false);
      }
    });
  });
});
