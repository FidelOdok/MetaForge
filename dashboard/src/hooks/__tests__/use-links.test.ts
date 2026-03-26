import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor } from '../../test/test-utils';
import { useNodeLink, useAllLinks, useCreateLink } from '../use-links';

vi.mock('../../api/endpoints/twin', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/endpoints/twin')>();
  const link = {
    id: 'link-001',
    node_id: 'node-001',
    file_path: '/tmp/model.step',
    tool: 'cadquery',
    watch: false,
    status: 'synced',
    last_synced_at: '2026-03-26T00:00:00Z',
    created_at: '2026-03-25T00:00:00Z',
  };
  return {
    ...actual,
    getNodeLink: vi.fn().mockResolvedValue(link),
    getAllLinks: vi.fn().mockResolvedValue([link]),
    createLink: vi.fn().mockResolvedValue(link),
    deleteLink: vi.fn().mockResolvedValue(undefined),
    syncNode: vi.fn().mockResolvedValue({
      link_id: 'link-001',
      node_id: 'node-001',
      status: 'synced',
      changes: {},
      synced_at: '2026-03-26T01:00:00Z',
    }),
  };
});

describe('useNodeLink', () => {
  it('returns link data for a node', async () => {
    const { result } = renderHook(() => useNodeLink('node-001'));
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.id).toBe('link-001');
    expect(result.current.data?.status).toBe('synced');
  });

  it('is disabled when nodeId is undefined', () => {
    const { result } = renderHook(() => useNodeLink(undefined));
    expect(result.current.fetchStatus).toBe('idle');
  });
});

describe('useAllLinks', () => {
  it('returns list of all links', async () => {
    const { result } = renderHook(() => useAllLinks());
    await waitFor(() => expect(result.current.data).toBeDefined());
    const links = result.current.data ?? [];
    expect(links).toHaveLength(1);
    const first = links[0] as (typeof links)[number] | undefined;
    expect(first?.node_id).toBe('node-001');
  });
});

describe('useCreateLink', () => {
  it('calls createLink API and returns the new link', async () => {
    const { result } = renderHook(() => useCreateLink('node-001'));
    result.current.mutate({ file_path: '/tmp/model.step', tool: 'cadquery', watch: false });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.id).toBe('link-001');
  });
});
