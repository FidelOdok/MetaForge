import { describe, it, expect, vi } from 'vitest';
import { renderHook, waitFor } from '../../test/test-utils';
import { useSessions, useSession } from '../use-sessions';

vi.mock('../../api/endpoints/sessions', () => ({
  getSessions: vi.fn().mockResolvedValue([
    { id: 's1', agentCode: 'MECH', taskType: 'validate_stress', status: 'completed', startedAt: '', events: [] },
  ]),
  getSession: vi.fn().mockResolvedValue(
    { id: 's1', agentCode: 'MECH', taskType: 'validate_stress', status: 'completed', startedAt: '', events: [] },
  ),
}));

describe('useSessions', () => {
  it('fetches session list', async () => {
    const { result } = renderHook(() => useSessions());
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data).toHaveLength(1);
  });
});

describe('useSession', () => {
  it('fetches single session', async () => {
    const { result } = renderHook(() => useSession('s1'));
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data?.agentCode).toBe('MECH');
  });

  it('is disabled when id is undefined', () => {
    const { result } = renderHook(() => useSession(undefined));
    expect(result.current.fetchStatus).toBe('idle');
  });
});
