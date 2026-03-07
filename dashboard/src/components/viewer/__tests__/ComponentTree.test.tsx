import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from '../../../test/test-utils';
import { ComponentTree } from '../ComponentTree';
import type { ModelManifest } from '../../../types/viewer';

const MOCK_MANIFEST: ModelManifest = {
  parts: [
    { name: 'Base Plate', meshName: 'mesh_0', children: [], boundingBox: { min: [0, 0, 0], max: [1, 1, 1] } },
    { name: 'Support Bracket', meshName: 'mesh_1', children: [], boundingBox: { min: [0, 0, 0], max: [1, 1, 1] } },
    { name: 'Top Cap', meshName: 'mesh_2', children: [], boundingBox: { min: [0, 0, 0], max: [1, 1, 1] } },
  ],
  meshToNodeMap: {},
  materials: [],
  stats: { triangleCount: 2400, fileSize: 48000 },
};

const mockSelectPart = vi.fn();
const mockToggleVisibility = vi.fn();

vi.mock('../../../store/viewer-store', () => ({
  useViewerStore: vi.fn((selector: (s: Record<string, unknown>) => unknown) => {
    const state = {
      manifest: MOCK_MANIFEST,
      selectedMeshName: null,
      hiddenMeshes: new Set(),
      selectPart: mockSelectPart,
      toggleVisibility: mockToggleVisibility,
    };
    return selector(state);
  }),
}));

describe('ComponentTree', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders tree from manifest parts', () => {
    const { getByText } = render(<ComponentTree />);
    expect(getByText('Base Plate')).toBeInTheDocument();
    expect(getByText('Support Bracket')).toBeInTheDocument();
    expect(getByText('Top Cap')).toBeInTheDocument();
  });

  it('shows triangle count in footer', () => {
    const { getByText } = render(<ComponentTree />);
    expect(getByText(/2,400 triangles/)).toBeInTheDocument();
  });

  it('filters nodes by search', () => {
    const { getByPlaceholderText, getByText, queryByText } = render(<ComponentTree />);
    const searchInput = getByPlaceholderText('Search parts...');

    // Simulate typing
    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    )!.set!;
    nativeInputValueSetter.call(searchInput, 'bracket');
    searchInput.dispatchEvent(new Event('change', { bubbles: true }));

    expect(getByText('Support Bracket')).toBeInTheDocument();
    expect(queryByText('Base Plate')).not.toBeInTheDocument();
    expect(queryByText('Top Cap')).not.toBeInTheDocument();
  });
});
