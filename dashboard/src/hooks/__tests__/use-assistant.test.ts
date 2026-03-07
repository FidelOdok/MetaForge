import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor } from '../../test/test-utils';
import { useProposals, useRunStatus } from '../use-assistant';

vi.mock('../../api/endpoints/assistant', () => ({
  getProposals: vi.fn().mockResolvedValue({ proposals: [], total: 0 }),
  getRunStatus: vi.fn().mockResolvedValue({ run_id: 'r1', status: 'completed', steps: {}, completed_at: null }),
  submitRequest: vi.fn(),
  decideProposal: vi.fn(),
}));

describe('useProposals', () => {
  it('fetches proposals', async () => {
    const { result } = renderHook(() => useProposals());
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.proposals).toEqual([]);
  });
});

describe('useRunStatus', () => {
  it('fetches run status', async () => {
    const { result } = renderHook(() => useRunStatus('r1'));
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.status).toBe('completed');
  });

  it('is disabled when runId is undefined', () => {
    const { result } = renderHook(() => useRunStatus(undefined));
    expect(result.current.fetchStatus).toBe('idle');
  });
});
