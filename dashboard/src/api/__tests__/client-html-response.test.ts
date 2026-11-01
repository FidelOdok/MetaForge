/**
 * Regression for the failure that took down app.metaforge.uk.
 *
 * The Vercel SPA catch-all rewrite matched `/api/v1/projects` and answered it
 * with `index.html` and a 200. Axios saw a success, `projects.ts` did
 * `data.projects.map(...)` on a string, and the page died in the ErrorBoundary
 * showing "Cannot read properties of undefined (reading 'map')" — a message
 * naming neither the cause nor the fix. Every page did the same thing, so a
 * dashboard with no gateway configured looked broken rather than unconfigured.
 *
 * `dashboard/vercel.json` now excludes those prefixes from the rewrite so they
 * 404 honestly. That fixes one host. Any reverse proxy, tunnel or captive
 * portal can serve HTML where JSON was expected, so the client refuses it on
 * principle rather than trusting the status code.
 *
 * Mocking follows `client-trace-context.test.ts`: a per-request adapter, so
 * the real interceptor chain runs and no HTTP-mocking dependency is added.
 */

import type { AxiosRequestConfig, AxiosResponse } from 'axios';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import apiClient from '../client';
import { resetGatewayCache, setGatewayBase } from '../../lib/gatewayConfig';

const SHELL = '<!DOCTYPE html><html><head><title>MetaForge</title></head><body></body></html>';

/** Issue a request whose "server" returns exactly the given body and headers. */
function requestReturning(
  data: unknown,
  headers: Record<string, string>,
  status = 200,
): Promise<AxiosResponse> {
  return apiClient.request({
    url: '/projects',
    adapter: async (config: AxiosRequestConfig) =>
      ({ data, status, statusText: 'OK', headers, config }) as never,
  });
}

describe('apiClient rejects HTML masquerading as data', () => {
  beforeEach(() => {
    localStorage.clear();
    resetGatewayCache();
  });

  afterEach(() => {
    localStorage.clear();
    resetGatewayCache();
  });

  it('rejects a 200 whose body is the SPA shell', async () => {
    await expect(
      requestReturning(SHELL, { 'content-type': 'text/html; charset=utf-8' }),
    ).rejects.toThrow();
  });

  it('names the missing gateway when none is configured', async () => {
    await expect(
      requestReturning(SHELL, { 'content-type': 'text/html; charset=utf-8' }),
    ).rejects.toThrow(/No gateway is configured/i);
  });

  it('names the address when one is configured but serves a website', async () => {
    setGatewayBase('https://example.com');
    await expect(
      requestReturning(SHELL, { 'content-type': 'text/html; charset=utf-8' }),
    ).rejects.toThrow(/example\.com/);
  });

  it('points at Settings, which is where the fix actually is', async () => {
    await expect(
      requestReturning(SHELL, { 'content-type': 'text/html; charset=utf-8' }),
    ).rejects.toThrow(/Settings/);
  });

  it('leaves genuine JSON alone', async () => {
    const response = await requestReturning(
      { projects: [], total: 0 },
      { 'content-type': 'application/json' },
    );
    expect(response.data).toEqual({ projects: [], total: 0 });
  });

  it('leaves a response with no content-type alone', async () => {
    // Gateways routinely omit it on a 204. Absence is not evidence of HTML,
    // and rejecting here would break every empty response.
    const response = await requestReturning(undefined, {}, 204);
    expect(response.status).toBe(204);
  });
});
