import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '../../test/test-utils';

vi.mock('../../hooks/use-twin', () => ({
  useTwinNodes: vi.fn(),
  useTwinNode: vi.fn(),
  useTwinRelationships: vi.fn(() => ({ data: [] })),
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
import { useTwinNodes, useTwinNode } from '../../hooks/use-twin';

const mockUseTwinNodes = vi.mocked(useTwinNodes);
const mockUseTwinNode = vi.mocked(useTwinNode);

describe('TwinViewerPage', () => {
  it('renders Digital Twin Viewer heading', () => {
    mockUseTwinNodes.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useTwinNodes>);
    mockUseTwinNode.mockReturnValue({ data: undefined, isLoading: false } as ReturnType<typeof useTwinNode>);
    render(<TwinViewerPage />);
    expect(screen.getByText('Digital Twin Viewer')).toBeInTheDocument();
  });

  it('shows graph view with empty state by default', () => {
    mockUseTwinNodes.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useTwinNodes>);
    mockUseTwinNode.mockReturnValue({ data: undefined, isLoading: false } as ReturnType<typeof useTwinNode>);
    render(<TwinViewerPage />);
    expect(screen.getByText('Empty twin')).toBeInTheDocument();
  });

  it('renders node list in graph mode', () => {
    mockUseTwinNodes.mockReturnValue({
      data: [
        { id: 'n1', name: 'bracket-v1.step', type: 'artifact', domain: 'mechanical', status: 'valid', properties: {}, updatedAt: new Date().toISOString() },
      ],
      isLoading: false,
    } as unknown as ReturnType<typeof useTwinNodes>);
    mockUseTwinNode.mockReturnValue({ data: undefined, isLoading: false } as ReturnType<typeof useTwinNode>);
    render(<TwinViewerPage />);
    expect(screen.getByText('bracket-v1.step')).toBeInTheDocument();
  });

  it('shows view mode toggle buttons', () => {
    mockUseTwinNodes.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useTwinNodes>);
    mockUseTwinNode.mockReturnValue({ data: undefined, isLoading: false } as ReturnType<typeof useTwinNode>);
    render(<TwinViewerPage />);
    expect(screen.getByText('3D Model')).toBeInTheDocument();
    expect(screen.getByText('Graph')).toBeInTheDocument();
  });
});
