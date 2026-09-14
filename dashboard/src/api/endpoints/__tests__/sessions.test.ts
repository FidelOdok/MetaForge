import { describe, it, expect, vi } from 'vitest';

vi.mock('../../client', () => ({
  default: {
    get: vi.fn(),
  },
}));

import apiClient from '../../client';
import { getSessions, getSession } from '../sessions';

const mockGet = vi.mocked(apiClient.get);

describe('getSessions', () => {
  it('maps snake_case to camelCase', async () => {
    mockGet.mockResolvedValueOnce({
      data: {
        sessions: [{
          id: 's1', agent_code: 'MECH', task_type: 'validate_stress',
          status: 'completed', started_at: '2024-01-01', completed_at: null,
          events: [{ id: 'e1', timestamp: '2024-01-01', type: 'task_started', agent_code: 'MECH', message: 'Started' }],
          run_id: 'r1',
        }],
        total: 1,
      },
    });

    const result = await getSessions();
    expect(result[0]?.agentCode).toBe('MECH');
    expect(result[0]?.taskType).toBe('validate_stress');
    expect(result[0]?.events[0]?.agentCode).toBe('MECH');
  });

  it('passes real MCP-capture event types through unchanged (MET-675)', async () => {
    // Regression: mapSession previously force-cast event.type to a closed
    // union of legacy workflow-run values via `as` -- a compile-time-only
    // assertion that doesn't actually validate or transform the runtime
    // value, so it silently "succeeded" even though real captured sessions
    // use a completely different vocabulary (thought/action/decision/
    // observation/error/result). This test would still pass with the old
    // buggy cast (TS `as` doesn't affect runtime), so it documents intent
    // rather than catching the type-level lie -- the real regression
    // coverage is in SessionDetailPage.test.tsx, which asserts the icon
    // actually renders for these values.
    mockGet.mockResolvedValueOnce({
      data: {
        sessions: [{
          id: 's2', agent_code: 'MECH', task_type: 'chat',
          status: 'abandoned', started_at: '2024-01-01', completed_at: null,
          events: [{ id: 'e1', timestamp: '2024-01-01', type: 'action', agent_code: 'MECH', message: 'twin.find_by_property failed' }],
          run_id: null,
        }],
        total: 1,
      },
    });

    const result = await getSessions();
    expect(result[0]?.status).toBe('abandoned');
    expect(result[0]?.events[0]?.type).toBe('action');
  });
});

describe('getSession', () => {
  it('returns undefined on error', async () => {
    mockGet.mockRejectedValueOnce(new Error('not found'));
    const result = await getSession('unknown');
    expect(result).toBeUndefined();
  });
});
