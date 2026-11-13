/**
 * A 401 to a request that carried a token ends the session (the provider
 * refreshes its own tokens, so the gateway has stopped accepting this one).
 * A 401 to a request that carried none does not: that is a gateway asking for
 * sign-in, which AuthGate already shows.
 */

import { AxiosError, type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';

import apiClient from '../client';
import { setAccessToken, setUnauthorizedHandler } from '../../auth/accessToken';

function failWith(status: number) {
  return apiClient.request({
    url: '/projects',
    adapter: async (config: AxiosRequestConfig) => {
      const internal = config as InternalAxiosRequestConfig;
      throw new AxiosError('failed', 'ERR_BAD_REQUEST', internal, null, {
        data: { detail: 'nope' },
        status,
        statusText: '',
        headers: {},
        config: internal,
      });
    },
  });
}

describe('apiClient and a rejected token', () => {
  afterEach(() => {
    setAccessToken(null);
    setUnauthorizedHandler(null);
  });

  it('reports a 401 to a request that carried a token', async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    setAccessToken('tok');
    await expect(failWith(401)).rejects.toThrow();
    expect(handler).toHaveBeenCalledOnce();
  });

  it('does not report a 401 without a token, or other failures', async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    await expect(failWith(401)).rejects.toThrow();
    setAccessToken('tok');
    await expect(failWith(403)).rejects.toThrow();
    await expect(failWith(404)).rejects.toThrow();
    expect(handler).not.toHaveBeenCalled();
  });
});
