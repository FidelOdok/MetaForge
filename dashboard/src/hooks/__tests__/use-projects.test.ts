import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor, act } from '../../test/test-utils';
import { useProjects, useProject, useUpdateProject, useDeleteProject } from '../use-projects';

vi.mock('../../api/endpoints/projects', () => ({
  getProjects: vi.fn().mockResolvedValue([
    { id: '1', name: 'P1', description: '', status: 'active', work_products: [], agentCount: 0, lastUpdated: '', createdAt: '' },
  ]),
  getProject: vi.fn().mockResolvedValue(
    { id: '1', name: 'P1', description: '', status: 'active', work_products: [], agentCount: 0, lastUpdated: '', createdAt: '' },
  ),
  updateProject: vi.fn().mockResolvedValue(
    { id: '1', name: 'Renamed', description: '', status: 'active', work_products: [], agentCount: 0, lastUpdated: '', createdAt: '' },
  ),
  deleteProject: vi.fn().mockResolvedValue(undefined),
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

describe('useUpdateProject', () => {
  it('updates a project', async () => {
    const { result } = renderHook(() => useUpdateProject());
    await act(async () => {
      result.current.mutate({ id: '1', payload: { name: 'Renamed' } });
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.name).toBe('Renamed');
  });
});

describe('useDeleteProject', () => {
  it('deletes a project', async () => {
    const { result } = renderHook(() => useDeleteProject());
    await act(async () => {
      result.current.mutate('1');
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });
});
