/**
 * A 401 to a request that carried a token ends the session (the provider
 * refreshes its own tokens, so the gateway has stopped accepting this one).
 * A 401 to a request that carried none does not: that is a gateway asking for
 * sign-in, which AuthGate already shows.
 */

import { AxiosError, type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios';
import { afterEach, describe, expect, it, vi } from 'vitest';

import apiClient from '../client';
import {
  setAccessToken,
  setTokenRefresher,
  setUnauthorizedHandler,
} from '../../auth/accessToken';

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
    setTokenRefresher(null);
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

  /** 401 unless the request carries `Bearer <accepted>`; counts what was sent. */
  function gatewayAccepting(accepted: string, seen: string[]) {
    return async (config: AxiosRequestConfig) => {
      const internal = config as InternalAxiosRequestConfig;
      const auth = String(internal.headers?.Authorization ?? '');
      seen.push(auth);
      if (auth === `Bearer ${accepted}`) {
        return { data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config: internal };
      }
      throw new AxiosError('expired', 'ERR_BAD_REQUEST', internal, null, {
        data: { detail: 'Token has expired' },
        status: 401,
        statusText: '',
        headers: {},
        config: internal,
      });
    };
  }

  it('renews an expired token and retries before ending the session', async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    setAccessToken('old');
    setTokenRefresher(async () => 'new');
    const seen: string[] = [];
    const res = await apiClient.request({ url: '/projects', adapter: gatewayAccepting('new', seen) });
    expect(res.data).toEqual({ ok: true });
    expect(seen).toEqual(['Bearer old', 'Bearer new']);
    expect(handler).not.toHaveBeenCalled();
  });

  it('ends the session when the refresh cannot renew it', async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    setAccessToken('old');
    setTokenRefresher(async () => null);
    await expect(
      apiClient.request({ url: '/projects', adapter: gatewayAccepting('never', []) }),
    ).rejects.toThrow();
    expect(handler).toHaveBeenCalledOnce();
  });

  it('retries once only: a renewed token the gateway also refuses ends the session', async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    setAccessToken('old');
    setTokenRefresher(async () => 'new');
    const seen: string[] = [];
    await expect(
      apiClient.request({ url: '/projects', adapter: gatewayAccepting('never', seen) }),
    ).rejects.toThrow();
    expect(seen).toEqual(['Bearer old', 'Bearer new']);
    expect(handler).toHaveBeenCalledOnce();
  });

  it('shares one refresh between requests that fail together', async () => {
    setAccessToken('old');
    const refresh = vi.fn(async () => {
      await new Promise((r) => setTimeout(r, 10));
      return 'new';
    });
    setTokenRefresher(refresh);
    const adapter = gatewayAccepting('new', []);
    await Promise.all([
      apiClient.request({ url: '/a', adapter }),
      apiClient.request({ url: '/b', adapter }),
      apiClient.request({ url: '/c', adapter }),
    ]);
    expect(refresh).toHaveBeenCalledOnce();
  });
});
