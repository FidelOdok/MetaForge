import { describe, it, expect, vi, beforeAll } from 'vitest';
import { render, screen } from '../../test/test-utils';

// ResizeObserver is not available in jsdom
beforeAll(() => {
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

vi.mock('../../hooks/use-twin', () => ({
  useTwinNodes: vi.fn(),
  useTwinNode: vi.fn(),
  useTwinRelationships: vi.fn(() => ({ data: [] })),
  useNodeVersionHistory: vi.fn(() => ({ data: undefined, isLoading: false })),
}));

vi.mock('../../hooks/use-scoped-chat', () => ({
  useScopedChat: () => ({
    thread: null,
    messages: [],
    isTyping: false,
    sendMessage: vi.fn(),
    createThread: vi.fn(),
    isLoading: false,
    isCreating: false,
    isSending: false,
  }),
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

import { TwinViewerPage } from '../TwinViewerPage';
import { useTwinNodes } from '../../hooks/use-twin';

const mockUseTwinNodes = vi.mocked(useTwinNodes);

describe('TwinViewerPage', () => {
  it('renders Digital Twin header', () => {
    mockUseTwinNodes.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useTwinNodes>);
    render(<TwinViewerPage />);
    expect(screen.getAllByText(/Digital Twin/).length).toBeGreaterThan(0);
  });

  it('shows empty state in graph view with no nodes', () => {
    mockUseTwinNodes.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useTwinNodes>);
    render(<TwinViewerPage />);
    expect(screen.getByText('Empty twin graph')).toBeInTheDocument();
  });

  it('renders node name in node list', () => {
    mockUseTwinNodes.mockReturnValue({
      data: [
        {
          id: 'n1',
          name: 'bracket-v1.step',
          type: 'work_product',
          domain: 'mechanical',
          status: 'valid',
          properties: {},
          updatedAt: new Date().toISOString(),
        },
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof useTwinNodes>);
    render(<TwinViewerPage />);
    expect(screen.getByText('bracket-v1.step')).toBeInTheDocument();
  });

  it('shows Graph and 3D view toggle buttons', () => {
    mockUseTwinNodes.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useTwinNodes>);
    render(<TwinViewerPage />);
    expect(screen.getByText('Graph')).toBeInTheDocument();
    expect(screen.getByText('3D')).toBeInTheDocument();
  });

  it('shows BOM strip at the bottom', () => {
    mockUseTwinNodes.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useTwinNodes>);
    render(<TwinViewerPage />);
    expect(screen.getAllByText('L1 Digital Thread').length).toBeGreaterThan(0);
  });
});
