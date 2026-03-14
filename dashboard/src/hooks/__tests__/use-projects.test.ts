import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor } from '../../test/test-utils';
import { useProjects, useProject } from '../use-projects';

vi.mock('../../api/endpoints/projects', () => ({
  getProjects: vi.fn().mockResolvedValue([
    { id: '1', name: 'P1', description: '', status: 'active', work_products: [], agentCount: 0, lastUpdated: '', createdAt: '' },
  ]),
  getProject: vi.fn().mockResolvedValue(
    { id: '1', name: 'P1', description: '', status: 'active', work_products: [], agentCount: 0, lastUpdated: '', createdAt: '' },
  ),
}));

describe('useProjects', () => {
  it('fetches project list', async () => {
    const { result } = renderHook(() => useProjects());
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data).toHaveLength(1);
  });
});

describe('useProject', () => {
  it('fetches single project', async () => {
    const { result } = renderHook(() => useProject('1'));
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.name).toBe('P1');
  });

  it('is disabled when id is undefined', () => {
    const { result } = renderHook(() => useProject(undefined));
    expect(result.current.fetchStatus).toBe('idle');
  });
});
