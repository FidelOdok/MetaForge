import { describe, it, expect, vi } from 'vitest';

vi.mock('../../client', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import apiClient from '../../client';
import { submitRequest, getRunStatus, getProposals } from '../assistant';

const mockGet = vi.mocked(apiClient.get);
const mockPost = vi.mocked(apiClient.post);

describe('submitRequest', () => {
  it('posts to /assistant/request', async () => {
    mockPost.mockResolvedValueOnce({ data: { request_id: 'r1', status: 'accepted', result: {}, errors: [] } });
    const result = await submitRequest({ action: 'validate_stress', target_id: 'art-001' });
    expect(mockPost).toHaveBeenCalledWith('/assistant/request', expect.objectContaining({ action: 'validate_stress' }));
    expect(result.status).toBe('accepted');
  });
});

describe('getRunStatus', () => {
  it('gets run status', async () => {
    mockGet.mockResolvedValueOnce({ data: { run_id: 'r1', status: 'completed', steps: {}, completed_at: null } });
    const result = await getRunStatus('r1');
    expect(result.status).toBe('completed');
  });
});

describe('getProposals', () => {
  it('gets proposals list', async () => {
    mockGet.mockResolvedValueOnce({ data: { proposals: [], total: 0 } });
    const result = await getProposals();
    expect(result.total).toBe(0);
  });
});
