import { describe, it, expect, vi } from 'vitest';

vi.mock('../../client', () => ({
  default: {
    get: vi.fn(),
  },
}));

import apiClient from '../../client';
import { getProjects, getProject } from '../projects';

const mockGet = vi.mocked(apiClient.get);

describe('getProjects', () => {
  it('maps snake_case to camelCase', async () => {
    mockGet.mockResolvedValueOnce({
      data: {
        projects: [{
          id: '1', name: 'Test', description: '', status: 'active',
          artifacts: [{ id: 'a1', name: 'S', type: 'schematic', status: 'valid', updated_at: '2024-01-01' }],
          agent_count: 2, last_updated: '2024-01-01', created_at: '2024-01-01',
        }],
        total: 1,
      },
    });

    const result = await getProjects();
    expect(result[0]?.agentCount).toBe(2);
    expect(result[0]?.lastUpdated).toBe('2024-01-01');
    expect(result[0]?.artifacts[0]?.updatedAt).toBe('2024-01-01');
  });
});

describe('getProject', () => {
  it('returns undefined on error', async () => {
    mockGet.mockRejectedValueOnce(new Error('not found'));
    const result = await getProject('unknown');
    expect(result).toBeUndefined();
  });
});
