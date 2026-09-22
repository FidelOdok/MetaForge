import { describe, it, expect, vi } from 'vitest';

vi.mock('../../client', () => ({
  default: {
    get: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

import apiClient from '../../client';
import { getProjects, getProject, updateProject, deleteProject } from '../projects';

const mockGet = vi.mocked(apiClient.get);
const mockPatch = vi.mocked(apiClient.patch);
const mockDelete = vi.mocked(apiClient.delete);

describe('getProjects', () => {
  it('maps snake_case to camelCase', async () => {
    mockGet.mockResolvedValueOnce({
      data: {
        projects: [{
          id: '1', name: 'Test', description: '', status: 'active',
          work_products: [{ id: 'a1', name: 'S', type: 'schematic', status: 'valid', updated_at: '2024-01-01' }],
          agent_count: 2, last_updated: '2024-01-01', created_at: '2024-01-01',
        }],
        total: 1,
      },
    });

    const result = await getProjects();
    expect(result[0]?.agentCount).toBe(2);
    expect(result[0]?.lastUpdated).toBe('2024-01-01');
    expect(result[0]?.work_products[0]?.updatedAt).toBe('2024-01-01');
  });
});

describe('getProject', () => {
  it('returns undefined only for an HTTP 404', async () => {
    mockGet.mockRejectedValueOnce({ isAxiosError: true, response: { status: 404 } });
    const result = await getProject('unknown');
    expect(result).toBeUndefined();
  });
});

describe('updateProject', () => {
  it('PATCHes the project and maps the response', async () => {
    mockPatch.mockResolvedValueOnce({
      data: {
        id: '1', name: 'Renamed', description: 'New desc', status: 'active',
        work_products: [], agent_count: 0, last_updated: '2024-01-02', created_at: '2024-01-01',
      },
    });

    const result = await updateProject('1', { name: 'Renamed', description: 'New desc' });
    expect(mockPatch).toHaveBeenCalledWith('/projects/1', { name: 'Renamed', description: 'New desc' });
    expect(result.name).toBe('Renamed');
    expect(result.lastUpdated).toBe('2024-01-02');
  });
});

describe('deleteProject', () => {
  it('DELETEs the project', async () => {
    mockDelete.mockResolvedValueOnce({});
    await deleteProject('1');
    expect(mockDelete).toHaveBeenCalledWith('/projects/1');
  });
});

it('preserves connection errors for the UI', async () => {
  mockGet.mockRejectedValueOnce(new Error('Network error'));
  await expect(getProject('p1')).rejects.toThrow('Network error');
});
it('accepts omitted fields that have OpenAPI defaults', async () => {
  mockGet.mockResolvedValueOnce({data:{projects:[{id:'p1',name:'Rover',description:'',status:'draft',created_at:'2026-09-21',last_updated:'2026-09-21'}],total:1}});
  const [project] = await getProjects();
  expect(project?.work_products).toEqual([]);
  expect(project?.agentCount).toBe(0);
});
