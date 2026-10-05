import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '../../test/test-utils';

vi.mock('../../hooks/use-twin', () => ({
  useTwinNodes: vi.fn(),
  useTwinNode: vi.fn(),
  useTwinRelationships: vi.fn(() => ({ data: [] })),
  useNodeVersionHistory: vi.fn(() => ({ data: [], isLoading: false })),
  useRevisionDiff: vi.fn(() => ({ data: undefined, isLoading: false, isError: false })),
  useGeometryDiff: vi.fn(() => ({ data: undefined, isLoading: false })),
}));

// FORGE-531: the inspector's inline preview reads the node's file.
vi.mock('../../api/endpoints/twin', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/endpoints/twin')>()),
  fetchNodeFileText: vi.fn(async () => '# Flight time target\n\nTwenty minutes.'),
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
import {
  useTwinNodes,
  useTwinNode,
  useTwinRelationships,
  useNodeVersionHistory,
  useRevisionDiff,
  useGeometryDiff,
} from '../../hooks/use-twin';
import { fireEvent, act, within } from '@testing-library/react';
import { useNavigate } from 'react-router-dom';
import { useProjectStore } from '../../store/project-store';
import { useLayoutStore } from '../../store/layout-store';
import { setSampleModeForTests } from '../../lib/sample-workspace';

const mockUseTwinNodes = vi.mocked(useTwinNodes);
const mockUseTwinNode = vi.mocked(useTwinNode);
const mockUseTwinRelationships = vi.mocked(useTwinRelationships);
const mockUseNodeVersionHistory = vi.mocked(useNodeVersionHistory);
const mockUseRevisionDiff = vi.mocked(useRevisionDiff);
const mockUseGeometryDiff = vi.mocked(useGeometryDiff);

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

  it('shows a revision compare picker when 2+ revisions exist, and diffs on selection (FORGE-301)', () => {
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
        { revision: 2, created_at: new Date().toISOString(), content_hash: 'abcdef1234', change_description: 'Widened mounting hole', metadata_snapshot: { wall_mm: 2 } },
        { revision: 1, created_at: new Date().toISOString(), content_hash: '0123456789', change_description: 'Initial import', metadata_snapshot: { wall_mm: 3 } },
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof useNodeVersionHistory>);
    mockUseRevisionDiff.mockReturnValue({
      data: {
        work_product_id: 'n1',
        revision_a: 1,
        revision_b: 2,
        changed: { wall_mm: { from_value: 3, to_value: 2 } },
        added: {},
        removed: {},
      },
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useRevisionDiff>);

    render(<TwinViewerPage />);
    fireEvent.click(screen.getByRole('button', { name: /bracket-v1\.step/ }));

    fireEvent.change(screen.getByTestId('revision-diff-select-a'), { target: { value: '1' } });
    fireEvent.change(screen.getByTestId('revision-diff-select-b'), { target: { value: '2' } });

    expect(screen.getByTestId('revision-diff-view')).toBeInTheDocument();
    expect(screen.getByText('wall_mm:')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('shows a real volume/area delta vs. a SUPERSEDES predecessor (FORGE-301)', () => {
    const node = {
      id: 'n1',
      name: 'enclosure-v2.step',
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
    mockUseGeometryDiff.mockReturnValue({
      data: {
        current_work_product_id: 'n1',
        previous_work_product_id: 'n0',
        current_volume_mm3: 1500,
        previous_volume_mm3: 1000,
        volume_delta_mm3: 500,
        current_area_mm2: 900,
        previous_area_mm2: 700,
        area_delta_mm2: 200,
        current_bounding_box: {},
        previous_bounding_box: {},
      },
      isLoading: false,
    } as unknown as ReturnType<typeof useGeometryDiff>);

    render(<TwinViewerPage />);
    fireEvent.click(screen.getByRole('button', { name: /enclosure-v2\.step/ }));

    expect(screen.getByTestId('geometry-version-diff')).toBeInTheDocument();
    expect(screen.getByText('1,000 mm³')).toBeInTheDocument();
    expect(screen.getByText('1,500 mm³')).toBeInTheDocument();
    expect(screen.getByText('(+500)')).toBeInTheDocument();
  });

  it('renders nothing for geometry diff when there is no SUPERSEDES predecessor', () => {
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
    mockUseGeometryDiff.mockReturnValue({ data: undefined, isLoading: false } as unknown as ReturnType<typeof useGeometryDiff>);

    render(<TwinViewerPage />);
    fireEvent.click(screen.getByRole('button', { name: /bracket-v1\.step/ }));

    expect(screen.queryByTestId('geometry-version-diff')).not.toBeInTheDocument();
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

describe('deep links (FORGE-371)', () => {
  // A reply that says "I committed the bracket" should be able to link to
  // the view that shows it. ?node= has worked since MET-514; ?tab= was
  // local state, so a link naming a tab landed on the page and silently
  // showed the default -- which reads as the link being wrong rather than
  // unsupported.
  it('opens on the tab named in the URL', () => {
    window.history.pushState({}, '', '/twin?tab=asm');
    render(<TwinViewerPage />);
    expect(screen.getByRole('button', { name: 'Assembly' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('falls back to the default tab for a name it does not know', () => {
    // The link may come from a build older or newer than this one. Still a
    // working page, not an error.
    window.history.pushState({}, '', '/twin?tab=nonsense');
    render(<TwinViewerPage />);
    expect(screen.getByRole('button', { name: 'Graph' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('leaves the default tab alone when no tab is named', () => {
    window.history.pushState({}, '', '/twin');
    render(<TwinViewerPage />);
    expect(screen.getByRole('button', { name: 'Graph' })).toHaveAttribute('aria-pressed', 'true');
  });
});

// ── FEA result inspector (FORGE-305) ────────────────────────────────────────
/** FORGE-246 landed the simulation_result work product; FORGE-279 the read
 *  route the Sim page lists from. The inspector never got its half, so a
 *  selected result rendered as untyped scalar rows -- `max_von_mises_mpa:
 *  182.4`, no units, no ordering, indistinguishable from the forty other
 *  properties beside it. */
describe('TwinViewerPage FEA result view (FORGE-305)', () => {
  const meshStats = { num_nodes: 12500, num_elements: 48000 };

  function resultNode(overrides: Record<string, unknown> = {}) {
    return {
      id: 'sr1',
      name: 'bracket-fea-run-3',
      type: 'work_product',
      domain: 'mechanical',
      status: 'valid',
      properties: {
        wp_type: 'simulation_result',
        max_von_mises_mpa: 182.43,
        max_displacement_mm: 0.4126,
        load_case: 'tip-load-500N',
      },
      meshStats,
      updatedAt: new Date().toISOString(),
      ...overrides,
    };
  }

  function selectNode(node: Record<string, unknown>) {
    window.history.pushState({}, '', '/twin');
    useLayoutStore.setState({ sidebarCollapsed: false });
    mockUseTwinNodes.mockReturnValue({
      data: [node],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useTwinNodes>);
    mockUseTwinNode.mockReturnValue({ data: node, isLoading: false } as unknown as ReturnType<typeof useTwinNode>);
    mockUseNodeVersionHistory.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useNodeVersionHistory>);
    render(<TwinViewerPage />);
    fireEvent.click(screen.getByRole('button', { name: /bracket-fea-run-3/ }));
  }

  it('shows the summary with units for a simulation_result node', () => {
    selectNode(resultNode());
    const panel = screen.getByTestId('fea-result-section');
    // The units are the point: 182.43 on its own is not an answer.
    expect(within(panel).getByText('182.4')).toBeInTheDocument();
    expect(within(panel).getByText('MPa')).toBeInTheDocument();
    expect(within(panel).getByText('0.413')).toBeInTheDocument();
    expect(within(panel).getByText('mm')).toBeInTheDocument();
  });

  it('names the load case that produced it', () => {
    selectNode(resultNode());
    expect(
      within(screen.getByTestId('fea-result-section')).getByText(/tip-load-500N/),
    ).toBeInTheDocument();
  });

  it('says so when the load case was not recorded, rather than showing a blank', () => {
    selectNode(resultNode({ properties: { wp_type: 'simulation_result', max_von_mises_mpa: 1 } }));
    expect(
      within(screen.getByTestId('fea-result-section')).getByText(/not recorded/),
    ).toBeInTheDocument();
  });

  it('shows mesh stats, which the scalar-only properties projection drops', () => {
    // The whole reason meshStats needed surfacing separately: `properties` is
    // scalar-only, so an object value never reaches the client at all.
    selectNode(resultNode());
    const panel = screen.getByTestId('fea-result-section');
    expect(within(panel).getByText('num_elements')).toBeInTheDocument();
    expect(within(panel).getByText('48,000')).toBeInTheDocument();
  });

  it('does not render for a node that is not a simulation result', () => {
    selectNode(resultNode({ properties: { wp_type: 'cad_model' } }));
    expect(screen.queryByTestId('fea-result-section')).not.toBeInTheDocument();
  });

  it('says the numbers are missing rather than rendering an empty panel', () => {
    // A blank panel reads as "the view is broken", which is the wrong
    // conclusion to lead a reader to.
    selectNode(resultNode({ properties: { wp_type: 'simulation_result' }, meshStats: undefined }));
    expect(
      within(screen.getByTestId('fea-result-section')).getByText(/never reached it/),
    ).toBeInTheDocument();
  });

  it('treats an empty-string measurement as absent, not as zero stress', () => {
    // `Number('')` is 0, which would render as a real measurement of zero --
    // the kind of fabricated reading that is worse than a dash.
    selectNode(
      resultNode({
        properties: { wp_type: 'simulation_result', max_von_mises_mpa: '', max_displacement_mm: 0.5 },
      }),
    );
    const panel = screen.getByTestId('fea-result-section');
    expect(within(panel).queryByText('0.0')).not.toBeInTheDocument();
    expect(within(panel).getByText('0.500')).toBeInTheDocument();
  });

  it('points at the Sim page for comparison instead of duplicating it', () => {
    selectNode(resultNode());
    const link = within(screen.getByTestId('fea-result-section')).getByRole('link', {
      name: /Compare in Sim/,
    });
    expect(link).toHaveAttribute('href', '/sim');
  });
});

describe('TwinViewerPage — inspector preview from the registry (FORGE-531)', () => {
  function selectWp(properties: Record<string, unknown>) {
    const node = {
      id: 'wp-prev',
      name: 'preview-target',
      type: 'work_product',
      domain: 'systems',
      status: 'valid',
      properties,
      updatedAt: new Date().toISOString(),
    };
    window.history.pushState({}, '', '/twin');
    useLayoutStore.setState({ sidebarCollapsed: false });
    mockUseTwinNodes.mockReturnValue({
      data: [node],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useTwinNodes>);
    mockUseTwinNode.mockReturnValue({ data: node, isLoading: false } as unknown as ReturnType<typeof useTwinNode>);
    mockUseNodeVersionHistory.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useNodeVersionHistory>);
    render(<TwinViewerPage />);
    fireEvent.click(screen.getByRole('button', { name: /preview-target/ }));
  }

  it('renders a PRD as Markdown in the inspector, not a raw dump', async () => {
    selectWp({ wp_type: 'prd', format: 'md' });
    const section = screen.getByTestId('node-preview-section');
    expect(within(section).getByText(/Preview · Markdown/)).toBeInTheDocument();
    expect(await within(section).findByRole('heading', { name: 'Flight time target' })).toBeInTheDocument();
  });

  it('leaves a STEP model to the 3D viewer and keeps the CAD actions', () => {
    selectWp({ wp_type: 'cad_model', format: 'step' });
    expect(screen.queryByTestId('node-preview-section')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /View 3D Model/ })).toBeInTheDocument();
    expect(screen.getByTitle('Boolean cut against another CAD node')).toBeInTheDocument();
  });

  it('offers 3D for an STL mesh, without the STEP-only boolean cut', () => {
    selectWp({ wp_type: 'manufacturing_file', format: 'stl' });
    expect(screen.getByRole('button', { name: /View 3D Model/ })).toBeInTheDocument();
    expect(screen.queryByTitle('Boolean cut against another CAD node')).not.toBeInTheDocument();
  });

  it('names the missing engine for an unknown format', () => {
    selectWp({ wp_type: 'manufacturing_file', format: 'weird' });
    const section = screen.getByTestId('node-preview-section');
    expect(within(section).getByText('No preview engine for .weird files')).toBeInTheDocument();
  });
});
