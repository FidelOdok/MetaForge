import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render } from '../../../test/test-utils';
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

const NESTED_MANIFEST: ModelManifest = {
  parts: [
    {
      name: 'Assembly',
      meshName: 'mesh_root',
      boundingBox: { min: [0, 0, 0], max: [1, 1, 1] },
      children: [
        { name: 'Screw A', meshName: 'mesh_screw_a', children: [], boundingBox: { min: [0, 0, 0], max: [1, 1, 1] } },
      ],
    },
  ],
  meshToNodeMap: {},
  materials: [],
  stats: { triangleCount: 100, fileSize: 1000 },
};

const mockSelectPart = vi.fn();
const mockToggleVisibility = vi.fn();
let mockManifest: ModelManifest = MOCK_MANIFEST;

vi.mock('../../../store/viewer-store', () => ({
  useViewerStore: vi.fn((selector: (s: Record<string, unknown>) => unknown) => {
    const state = {
      manifest: mockManifest,
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
    mockManifest = MOCK_MANIFEST;
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

  it('"Collapse all" / "Expand all" actually toggles node expansion (regression: used to be a no-op)', () => {
    mockManifest = NESTED_MANIFEST;
    const { getByText, queryByText, getByTitle } = render(<ComponentTree />);

    // Starts expanded by default.
    expect(getByText('Screw A')).toBeInTheDocument();

    fireEvent.click(getByTitle('Collapse all'));
    expect(queryByText('Screw A')).not.toBeInTheDocument();

    fireEvent.click(getByTitle('Expand all'));
    expect(getByText('Screw A')).toBeInTheDocument();
  });
});
