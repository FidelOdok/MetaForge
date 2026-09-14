/**
 * The API client must actually inject W3C trace context (MET-736).
 *
 * This existed as a `require('@opentelemetry/api')` inside a try/catch.
 * `require` is undefined in a browser ESM module and Vite does not shim it for
 * application code, so it survived verbatim into the bundle, threw
 * ReferenceError on its first line, and the catch swallowed it. No
 * `traceparent` header was ever sent, so browser spans never linked to gateway
 * traces -- silently, for as long as the code existed.
 *
 * What this test covers, and what it deliberately does not:
 *
 * It asserts the interceptor calls `propagation.inject` and copies the
 * resulting carrier onto the outgoing headers. That is exactly the code that
 * was dead, and asserting the header is present is the only thing that would
 * have caught it -- a test that the module imports, or that the interceptor is
 * registered, would have passed throughout.
 *
 * It does NOT exercise async context propagation. A first draft tried to, via
 * `context.with()` around an awaited request, and failed against the *fixed*
 * code: `@opentelemetry/api` needs a registered ContextManager, and the
 * stack-based one does not survive an `await` (the app uses ZoneContextManager
 * for that reason). Reproducing zone.js here would test OTel's plumbing rather
 * than ours, and its failure looks identical to the bug being unfixed -- which
 * is a trap worth naming rather than leaving for the next reader.
 */

import { propagation, type TextMapPropagator } from '@opentelemetry/api';
import type { AxiosRequestConfig } from 'axios';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import apiClient from '../client';

const EXPECTED_TRACEPARENT = '00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01';

/** A propagator that injects a known header, so the assertion is exact. */
class FixedPropagator implements TextMapPropagator {
  constructor(private readonly carrier: Record<string, string>) {}

  inject(_ctx: unknown, target: unknown, setter: { set(t: unknown, k: string, v: string): void }) {
    for (const [key, value] of Object.entries(this.carrier)) {
      setter.set(target, key, value);
    }
  }

  extract(ctx: unknown) {
    return ctx as never;
  }

  fields() {
    return Object.keys(this.carrier);
  }
}

/** Capture the config the interceptor chain produces, without a network call. */
async function captureRequestConfig(): Promise<AxiosRequestConfig> {
  let captured: AxiosRequestConfig | undefined;

  await apiClient.request({
    url: '/anything',
    adapter: async (config) => {
      captured = config;
      return {
        data: {},
        status: 200,
        statusText: 'OK',
        headers: {},
        config: config as never,
      };
    },
  });

  if (!captured) throw new Error('adapter never ran; nothing captured');
  return captured;
}

function headerOf(config: AxiosRequestConfig, name: string): string | undefined {
  const headers = config.headers as
    | { get?(k: string): unknown; [k: string]: unknown }
    | undefined;
  if (!headers) return undefined;
  // axios normalises into AxiosHeaders, which exposes get(); fall back to a
  // plain property read so this does not depend on that internal detail.
  const viaGet = typeof headers.get === 'function' ? headers.get(name) : undefined;
  return (viaGet ?? headers[name]) as string | undefined;
}

describe('apiClient trace-context injection (MET-736)', () => {
  beforeAll(() => {
    propagation.setGlobalPropagator(
      new FixedPropagator({ traceparent: EXPECTED_TRACEPARENT }),
    );
  });

  afterAll(() => {
    propagation.disable();
  });

  it('copies the injected carrier onto the outgoing request headers', async () => {
    const config = await captureRequestConfig();

    expect(
      headerOf(config, 'traceparent'),
      'no traceparent header — trace context is not reaching the gateway',
    ).toBe(EXPECTED_TRACEPARENT);
  });

  it('still sends the request when the propagator injects nothing', async () => {
    // No span, nothing to propagate: the request must go out regardless.
    //
    // disable() first: OTel's registerGlobal refuses to overwrite an
    // already-registered global and keeps the original, so without this the
    // swap is silently ignored and this test sees the previous propagator's
    // header. (Which is how it first failed -- another silent no-op.)
    propagation.disable();
    propagation.setGlobalPropagator(new FixedPropagator({}));

    const config = await captureRequestConfig();

    expect(headerOf(config, 'traceparent')).toBeUndefined();
    expect(config.url).toBe('/anything');
  });
});
