import axios from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { probeGateway } from '../health';
import { resetGatewayCache } from '../../../lib/gatewayConfig';

/**
 * "Test connection" must name the cause it can already work out.
 *
 * The old failure message read: "Could not reach <url>. The gateway may be
 * down, blocked by CORS, or blocked by the browser as mixed content." Three
 * possibilities, least likely first.
 *
 * That cost real time. A gateway on Tailscale at http://fidel-dev:8000 was
 * healthy, answering, and sending `access-control-allow-origin: *` — but the
 * dashboard was open on https://app.metaforge.uk, so the browser refused the
 * plain-HTTP request before it left. The message sent the user looking for a
 * dead gateway twice.
 *
 * A browser reports a blocked request to JS exactly as it reports a dead
 * host: no status, no response, no detail. But the page's own scheme and the
 * target's are both known locally, so when that combination explains the
 * failure, the probe should say so instead of offering a menu.
 */

function onPage(origin: string) {
  const url = new URL(origin);
  Object.defineProperty(window, 'location', {
    value: { ...window.location, protocol: url.protocol, host: url.host, origin: url.origin },
    writable: true,
    configurable: true,
  });
}

/** A network-level failure: what axios reports for a blocked request. */
function networkFailure() {
  const err = Object.assign(new Error('Network Error'), {
    isAxiosError: true,
    response: undefined,
    code: 'ERR_NETWORK',
  });
  vi.spyOn(axios, 'get').mockRejectedValue(err);
  vi.spyOn(axios, 'isAxiosError').mockReturnValue(true);
}

describe('probeGateway names the cause it can determine', () => {
  const realLocation = window.location;

  beforeEach(() => {
    localStorage.clear();
    resetGatewayCache();
    networkFailure();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    Object.defineProperty(window, 'location', { value: realLocation, writable: true });
    localStorage.clear();
    resetGatewayCache();
  });

  it('identifies mixed content instead of blaming the gateway', async () => {
    // The exact case that wasted the time: HTTPS dashboard, HTTP gateway.
    onPage('https://app.metaforge.uk');
    await expect(probeGateway('http://fidel-dev:8000')).rejects.toThrow(/mixed content/i);
  });

  it('does not claim the gateway may be down when the browser blocked it', async () => {
    onPage('https://app.metaforge.uk');
    await expect(probeGateway('http://fidel-dev:8000')).rejects.not.toThrow(/may be down/i);
  });

  it('suggests HTTPS, which is the actual fix', async () => {
    onPage('https://app.metaforge.uk');
    await expect(probeGateway('http://fidel-dev:8000')).rejects.toThrow(/HTTPS/);
  });

  it('notes that Chrome tolerates loopback but other browsers do not', async () => {
    onPage('https://app.metaforge.uk');
    await expect(probeGateway('http://localhost:8000')).rejects.toThrow(/Chrome/);
  });

  it('identifies the dashboard being pointed at itself', async () => {
    // The first mistake made: :3000 is the dev dashboard, :8000 the gateway.
    onPage('http://fidel-dev:3000');
    await expect(probeGateway('http://fidel-dev:3000')).rejects.toThrow(
      /this dashboard, not a gateway/i,
    );
  });

  it('falls back to an honest unknown when neither applies', async () => {
    // Same scheme, different origin: the browser did send it, so the cause
    // really is unknown from here. Say that rather than inventing one.
    onPage('http://fidel-dev:3000');
    const err = await probeGateway('http://fidel-dev:8000').catch((e: Error) => e);
    expect(err.message).toMatch(/Could not reach/);
    expect(err.message).not.toMatch(/mixed content/i);
  });

  it('still reports a real HTTP error status from the gateway', async () => {
    vi.spyOn(axios, 'get').mockRejectedValue(
      Object.assign(new Error('Request failed'), {
        isAxiosError: true,
        response: { status: 502, statusText: 'Bad Gateway' },
      }),
    );
    onPage('http://fidel-dev:3000');
    await expect(probeGateway('http://fidel-dev:8000')).rejects.toThrow(/502/);
  });
});
