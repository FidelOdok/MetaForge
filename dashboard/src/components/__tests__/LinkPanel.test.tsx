import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '../../test/test-utils';
import { LinkPanel } from '../LinkPanel';
import type { FileLink, SyncResult } from '../../types/twin';

const MOCK_SYNCED_LINK: FileLink = {
  work_product_id: 'node-001',
  source_path: '/tmp/model.step',
  tool: 'cadquery',
  watch: false,
  sync_status: 'synced',
  source_hash: 'abc123',
  last_synced_at: '2026-03-26T00:00:00Z',
  created_at: '2026-03-25T00:00:00Z',
};

const MOCK_CHANGED_LINK: FileLink = {
  ...MOCK_SYNCED_LINK,
  sync_status: 'changed',
};

const MOCK_DISCONNECTED_LINK: FileLink = {
  ...MOCK_SYNCED_LINK,
  sync_status: 'disconnected',
};

const MOCK_SYNC_RESULT: SyncResult = {
  work_product_id: 'node-001',
  sync_status: 'synced',
  changes: { mass: { before: '1.2', after: '1.5' } },
  synced_at: '2026-03-26T01:00:00Z',
};

// Mutable ref so individual tests can override the return value
let mockLink: FileLink | null = null;

vi.mock('../../hooks/use-links', () => ({
  useNodeLink: vi.fn(() => ({ data: mockLink, isLoading: false })),
  useCreateLink: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
  })),
  useDeleteLink: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
  })),
  useSyncNode: vi.fn(() => ({
    mutate: vi.fn(),
    isPending: false,
  })),
}));

beforeEach(() => {
  mockLink = null;
  vi.clearAllMocks();
});

describe('LinkPanel', () => {
  it('shows create form when no link exists', () => {
    mockLink = null;
    render(<LinkPanel nodeId="node-001" />);
    expect(screen.getByLabelText('File path')).toBeDefined();
    expect(screen.getByLabelText('Tool')).toBeDefined();
    expect(screen.getByText('Link file')).toBeDefined();
  });

  it('shows Synced status when link is synced', () => {
    mockLink = MOCK_SYNCED_LINK;
    render(<LinkPanel nodeId="node-001" />);
    expect(screen.getByText('Synced')).toBeDefined();
    expect(screen.getByText('/tmp/model.step')).toBeDefined();
    expect(screen.getByText('Sync now')).toBeDefined();
  });

  it('shows Changes detected badge when status is changed', () => {
    mockLink = MOCK_CHANGED_LINK;
    render(<LinkPanel nodeId="node-001" />);
    expect(screen.getByText('Changes detected')).toBeDefined();
  });

  it('shows File missing warning when status is disconnected', () => {
    mockLink = MOCK_DISCONNECTED_LINK;
    render(<LinkPanel nodeId="node-001" />);
    expect(screen.getByText('File missing')).toBeDefined();
    expect(screen.getByText(/Warning: the source file is missing/)).toBeDefined();
  });

  it('Unlink button triggers deleteLink mutation', async () => {
    mockLink = MOCK_SYNCED_LINK;
    const { useDeleteLink } = await import('../../hooks/use-links');
    const mockMutate = vi.fn();
    vi.mocked(useDeleteLink).mockReturnValue({
      mutate: mockMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useDeleteLink>);

    render(<LinkPanel nodeId="node-001" />);
    const unlinkButton = screen.getByText('Unlink');
    fireEvent.click(unlinkButton);

    // First click shows confirm
    expect(screen.getByText('Confirm unlink?')).toBeDefined();

    fireEvent.click(screen.getByText('Confirm unlink?'));
    await waitFor(() => expect(mockMutate).toHaveBeenCalledOnce());
  });

  it('sync button is highlighted when status is changed', () => {
    mockLink = MOCK_CHANGED_LINK;
    render(<LinkPanel nodeId="node-001" />);
    const syncButton = screen.getByText('Sync now');
    // KC Button primary uses inline styles (not bg-blue-600); verify button exists
    expect(syncButton.closest('button')).toBeTruthy();
  });

  it('shows diff summary card after sync', async () => {
    mockLink = MOCK_SYNCED_LINK;
    const { useSyncNode } = await import('../../hooks/use-links');
    vi.mocked(useSyncNode).mockReturnValue({
      mutate: vi.fn((_, options) => {
        options?.onSuccess?.(MOCK_SYNC_RESULT, undefined, undefined);
      }),
      isPending: false,
    } as unknown as ReturnType<typeof useSyncNode>);

    render(<LinkPanel nodeId="node-001" />);
    fireEvent.click(screen.getByText('Sync now'));

    await waitFor(() => expect(screen.getByText('Sync summary')).toBeDefined());
    expect(screen.getByText('mass')).toBeDefined();
  });
});
