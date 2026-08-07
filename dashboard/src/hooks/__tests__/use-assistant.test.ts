import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor } from '../../test/test-utils';
import { useProposals } from '../use-assistant';

vi.mock('../../api/endpoints/assistant', () => ({
  getProposals: vi.fn().mockResolvedValue({ proposals: [], total: 0 }),
  decideProposal: vi.fn(),
}));

describe('useProposals', () => {
  it('fetches proposals', async () => {
    const { result } = renderHook(() => useProposals());
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.proposals).toEqual([]);
  });
});
