import { describe, it, expect, vi } from 'vitest';

vi.mock('axios', () => ({
  default: {
    get: vi.fn(),
    isAxiosError: () => false,
  },
}));

import axios from 'axios';
import { getHealth, probeGateway } from '../health';

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

it('rejects an HTML static-host fallback as gateway health', async () => {
  mockGet.mockResolvedValueOnce({data:'<!doctype html><html>App</html>'});
  await expect(probeGateway('https://example.invalid')).rejects.toThrow('gateway health response');
});
it('applies optional health defaults from the API schema', async () => {
  mockGet.mockResolvedValueOnce({data:{status:'degraded',uptime_seconds:10,timestamp:'2026-09-21T10:00:00Z'}});
  const result = await getHealth();
  expect(result.components).toEqual([]);
  expect(result.version).toBe('0.1.0');
});
