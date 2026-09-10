import { describe, it, expect, vi } from 'vitest';

vi.mock('axios', () => ({
  default: {
    get: vi.fn(),
  },
}));

import axios from 'axios';
import { getHealth } from '../health';

const mockGet = vi.mocked(axios.get);

describe('getHealth', () => {
  it('fetches the bare /health path (not under /api/v1)', async () => {
    mockGet.mockResolvedValueOnce({
      data: {
        status: 'healthy',
        components: [],
        timestamp: new Date().toISOString(),
        uptime_seconds: 10,
        version: '0.1.0',
      },
    });
    const result = await getHealth();
    expect(mockGet).toHaveBeenCalledWith('/health', expect.objectContaining({ timeout: expect.any(Number) }));
    expect(result.status).toBe('healthy');
  });
});
