import { describe, it, expect, vi } from 'vitest';

vi.mock('../../client', () => ({
  default: {
    get: vi.fn(),
  },
}));

import apiClient from '../../client';
import { getTwinNodes } from '../twin';

const mockGet = vi.mocked(apiClient.get);

const NODE = {
  id: 'n1',
  name: 'Clip Panel',
  type: 'work_product',
  domain: 'mechanical',
  status: 'valid',
  properties: {},
  updatedAt: '2026-06-12T00:00:00Z',
};

describe('getTwinNodes (MET-491 project scoping)', () => {
  it('omits project_id params when no project is selected', async () => {
    mockGet.mockResolvedValueOnce({ data: { nodes: [NODE], total: 1 } });

    const result = await getTwinNodes();

    expect(result).toHaveLength(1);
    // No params object => global (all projects) behaviour preserved.
    expect(mockGet).toHaveBeenCalledWith('/twin/nodes', { params: undefined });
  });

  it('passes project_id as a query param when scoped to a project', async () => {
    mockGet.mockResolvedValueOnce({ data: { nodes: [NODE], total: 1 } });

    await getTwinNodes('f8240b2a-9e01-4b16-83eb-b24cfcd4a04f');

    expect(mockGet).toHaveBeenCalledWith('/twin/nodes', {
      params: { project_id: 'f8240b2a-9e01-4b16-83eb-b24cfcd4a04f' },
    });
  });
});
