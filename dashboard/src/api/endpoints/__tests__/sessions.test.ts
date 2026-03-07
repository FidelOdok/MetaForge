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
});

describe('getSession', () => {
  it('returns undefined on error', async () => {
    mockGet.mockRejectedValueOnce(new Error('not found'));
    const result = await getSession('unknown');
    expect(result).toBeUndefined();
  });
});
