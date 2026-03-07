import { describe, it, expect, vi } from 'vitest';
import { render } from '../../../test/test-utils';
import { BomAnnotationPanel } from '../BomAnnotationPanel';
import type { ModelManifest } from '../../../types/viewer';

const MOCK_MANIFEST: ModelManifest = {
  parts: [
    { name: 'Base Plate', meshName: 'mesh_0', children: [], boundingBox: { min: [0, 0, 0], max: [1, 1, 1] } },
  ],
  meshToNodeMap: { mesh_0: 'node-001' },
  materials: [],
  stats: { triangleCount: 100, fileSize: 1000 },
};

let mockViewerState: Record<string, unknown> = {
  selectedMeshName: null,
  manifest: null,
};

vi.mock('../../../store/viewer-store', () => ({
  useViewerStore: vi.fn((selector: (s: Record<string, unknown>) => unknown) => selector(mockViewerState)),
}));

vi.mock('../../../hooks/use-twin', () => ({
  useTwinNode: vi.fn(() => ({ data: null })),
  useTwinNodes: vi.fn(() => ({ data: [] })),
  useTwinRelationships: vi.fn(() => ({ data: [] })),
}));

vi.mock('../../../hooks/use-bom', () => ({
  useBom: vi.fn(() => ({ data: [] })),
}));

vi.mock('../../../hooks/use-scoped-chat', () => ({
  useScopedChat: vi.fn(() => ({
    thread: null,
    messages: [],
    isTyping: false,
    sendMessage: vi.fn(),
    createThread: vi.fn(),
  })),
}));

vi.mock('../../chat/integrations/NodeChatPanel', () => ({
  NodeChatPanel: () => <div data-testid="node-chat-panel" />,
}));

describe('BomAnnotationPanel', () => {
  it('shows empty state when no mesh is selected', () => {
    mockViewerState = { selectedMeshName: null, manifest: null };
    const { getByText } = render(<BomAnnotationPanel />);
    expect(getByText(/click a part/i)).toBeInTheDocument();
  });

  it('shows part info when mesh is selected', () => {
    mockViewerState = { selectedMeshName: 'mesh_0', manifest: MOCK_MANIFEST };
    const { getByText } = render(<BomAnnotationPanel />);
    expect(getByText('Base Plate')).toBeInTheDocument();
  });
});
