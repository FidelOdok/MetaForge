import { describe, it, expect, vi } from 'vitest';

vi.mock('../../client', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import apiClient from '../../client';
import { getProposals, decideProposal, createProposal } from '../assistant';

const mockGet = vi.mocked(apiClient.get);
const mockPost = vi.mocked(apiClient.post);

describe('getProposals', () => {
  it('gets proposals list', async () => {
    mockGet.mockResolvedValueOnce({ data: { proposals: [], total: 0 } });
    const result = await getProposals();
    expect(result.total).toBe(0);
  });
});

describe('createProposal', () => {
  it('posts a human-created proposal', async () => {
    mockPost.mockResolvedValueOnce({
      data: { change_id: 'c2', status: 'pending', agent_code: 'human' },
    });
    const result = await createProposal({
      description: 'Widen the bracket pad',
      diff: { action: 'regenerate_geometry', script_source: 'pad(20)\n' },
      projectId: 'proj-1',
    });
    expect(mockPost).toHaveBeenCalledWith(
      '/assistant/proposals',
      expect.objectContaining({
        description: 'Widen the bracket pad',
        diff: { action: 'regenerate_geometry', script_source: 'pad(20)\n' },
        project_id: 'proj-1',
      }),
    );
    expect(result.change_id).toBe('c2');
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
