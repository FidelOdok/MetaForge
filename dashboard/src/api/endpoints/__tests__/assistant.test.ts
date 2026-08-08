import { describe, it, expect, vi } from 'vitest';

vi.mock('../../client', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import apiClient from '../../client';
import { getProposals, decideProposal } from '../assistant';

const mockGet = vi.mocked(apiClient.get);
const mockPost = vi.mocked(apiClient.post);

describe('getProposals', () => {
  it('gets proposals list', async () => {
    mockGet.mockResolvedValueOnce({ data: { proposals: [], total: 0 } });
    const result = await getProposals();
    expect(result.total).toBe(0);
  });
});

describe('decideProposal', () => {
  it('posts a decision to the decide endpoint', async () => {
    mockPost.mockResolvedValueOnce({
      data: { change_id: 'c1', status: 'approved' },
    });
    const result = await decideProposal('c1', 'approve', 'looks good', 'dashboard-user');
    expect(mockPost).toHaveBeenCalledWith(
      '/assistant/proposals/c1/decide',
      expect.objectContaining({ change_id: 'c1', decision: 'approve' }),
    );
    expect(result.status).toBe('approved');
  });
});
