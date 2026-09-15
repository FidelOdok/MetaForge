import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  GATEWAY_STORAGE_KEY,
  GatewayUrlError,
  apiBase,
  apiUrl,
  describeMixedContent,
  gatewayUrl,
  getGatewayBase,
  isGatewayUserConfigured,
  joinAddressPort,
  normalizeGatewayBase,
  resetGatewayCache,
  resolveGatewayHref,
  setGatewayBase,
  splitGatewayBase,
  subscribeGatewayBase,
} from '../gatewayConfig';

/** jsdom serves the page over http://localhost by default. */
function setPageProtocol(protocol: 'http:' | 'https:', host = 'localhost:3000') {
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { protocol, host, hostname: host.split(':')[0], origin: `${protocol}//${host}` },
  });
}

beforeEach(() => {
  localStorage.clear();
  resetGatewayCache();
  setPageProtocol('http:');
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe('normalizeGatewayBase', () => {
  it('returns empty for blank input — meaning same-origin proxy mode', () => {
    expect(normalizeGatewayBase('')).toBe('');
    expect(normalizeGatewayBase('   ')).toBe('');
  });

  it('adds http:// to a bare host:port on an http page', () => {
    expect(normalizeGatewayBase('localhost:8000')).toBe('http://localhost:8000');
    expect(normalizeGatewayBase('fidel-dev:8000')).toBe('http://fidel-dev:8000');
  });

  it('defaults a bare non-loopback host to https when the page is https', () => {
    setPageProtocol('https:', 'app.example.com');
    expect(normalizeGatewayBase('gw.example.ts.net')).toBe('https://gw.example.ts.net');
  });

  it('keeps loopback on http even when the page is https', () => {
    setPageProtocol('https:', 'app.example.com');
    expect(normalizeGatewayBase('localhost:8000')).toBe('http://localhost:8000');
    expect(normalizeGatewayBase('127.0.0.1:8000')).toBe('http://127.0.0.1:8000');
  });

  it('preserves an explicit scheme and strips a trailing slash', () => {
    expect(normalizeGatewayBase('https://gw.example.com/')).toBe('https://gw.example.com');
    expect(normalizeGatewayBase('http://gw.example.com:9000//')).toBe('http://gw.example.com:9000');
  });

  it('keeps a path prefix for a reverse-proxied gateway', () => {
    expect(normalizeGatewayBase('https://example.com/metaforge/')).toBe(
      'https://example.com/metaforge',
    );
  });

  it('rejects a pasted API path rather than building /v1/v1', () => {
    for (const bad of [
      'https://gw.example.com/api/v1',
      'https://gw.example.com/v1',
      'https://gw.example.com/api',
    ]) {
      expect(() => normalizeGatewayBase(bad)).toThrow(GatewayUrlError);
    }
  });

  it('rejects unsupported schemes, queries and fragments', () => {
    expect(() => normalizeGatewayBase('ftp://gw.example.com')).toThrow(GatewayUrlError);
    expect(() => normalizeGatewayBase('https://gw.example.com?x=1')).toThrow(GatewayUrlError);
    expect(() => normalizeGatewayBase('https://gw.example.com#frag')).toThrow(GatewayUrlError);
  });
});

describe('joinAddressPort', () => {
  it('combines the two form fields', () => {
    expect(joinAddressPort('http://localhost', '8000')).toBe('http://localhost:8000');
  });

  it('lets the explicit port field override one embedded in the address', () => {
    expect(joinAddressPort('http://localhost:1234', '8000')).toBe('http://localhost:8000');
  });

  it('keeps the address port when the port field is empty', () => {
    expect(joinAddressPort('http://localhost:8000', '')).toBe('http://localhost:8000');
  });

  it('is empty when both fields are empty', () => {
    expect(joinAddressPort('', '')).toBe('');
  });

  it('rejects a port with no address, and an out-of-range port', () => {
    expect(() => joinAddressPort('', '8000')).toThrow(GatewayUrlError);
    expect(() => joinAddressPort('http://localhost', '0')).toThrow(GatewayUrlError);
    expect(() => joinAddressPort('http://localhost', '70000')).toThrow(GatewayUrlError);
    expect(() => joinAddressPort('http://localhost', 'abc')).toThrow(GatewayUrlError);
  });
});

describe('splitGatewayBase', () => {
  it('round-trips through joinAddressPort', () => {
    const base = 'https://gw.example.com:8443/metaforge';
    const { address, port } = splitGatewayBase(base);
    expect(address).toBe('https://gw.example.com/metaforge');
    expect(port).toBe('8443');
    expect(joinAddressPort(address, port)).toBe(base);
  });

  it('returns empty fields for an unset gateway', () => {
    expect(splitGatewayBase('')).toEqual({ address: '', port: '' });
  });
});

describe('unconfigured gateway keeps the original proxy-relative paths', () => {
  it('uses /api/v1 — the prefix Vite and nginx rewrite', () => {
    expect(getGatewayBase()).toBe('');
    expect(apiBase()).toBe('/api/v1');
    expect(apiUrl('/twin/nodes/n1/file')).toBe('/api/v1/twin/nodes/n1/file');
    expect(gatewayUrl('/health')).toBe('/health');
  });
});

describe('configured gateway targets the gateway’s own /v1 mount', () => {
  beforeEach(() => setGatewayBase('https://gw.example.com:8000'));

  it('drops the /api proxy segment, which the gateway does not serve', () => {
    expect(apiBase()).toBe('https://gw.example.com:8000/v1');
    expect(apiUrl('/twin/nodes/n1/file')).toBe('https://gw.example.com:8000/v1/twin/nodes/n1/file');
  });

  it('puts bare-root paths like /health at the gateway root', () => {
    expect(gatewayUrl('/health')).toBe('https://gw.example.com:8000/health');
  });

  it('accepts a path with no leading slash', () => {
    expect(apiUrl('runs')).toBe('https://gw.example.com:8000/v1/runs');
  });
});

describe('resolveGatewayHref', () => {
  it('prefixes /api in proxy mode', () => {
    expect(resolveGatewayHref('/v1/convert/abc/glb')).toBe('/api/v1/convert/abc/glb');
  });

  it('prefixes the gateway origin when one is configured', () => {
    setGatewayBase('https://gw.example.com:8000');
    expect(resolveGatewayHref('/v1/convert/abc/glb')).toBe(
      'https://gw.example.com:8000/v1/convert/abc/glb',
    );
  });

  it('leaves absolute URLs and non-/v1 paths alone', () => {
    setGatewayBase('https://gw.example.com:8000');
    expect(resolveGatewayHref('https://cdn.example.com/a.glb')).toBe('https://cdn.example.com/a.glb');
    expect(resolveGatewayHref('//cdn.example.com/a.glb')).toBe('//cdn.example.com/a.glb');
    expect(resolveGatewayHref('/static/a.glb')).toBe('/static/a.glb');
    expect(resolveGatewayHref('')).toBe('');
  });
});

describe('persistence', () => {
  it('stores, reports and clears the override', () => {
    expect(isGatewayUserConfigured()).toBe(false);
    setGatewayBase('localhost:8000');
    expect(localStorage.getItem(GATEWAY_STORAGE_KEY)).toBe('http://localhost:8000');
    expect(isGatewayUserConfigured()).toBe(true);

    setGatewayBase(null);
    expect(localStorage.getItem(GATEWAY_STORAGE_KEY)).toBeNull();
    expect(isGatewayUserConfigured()).toBe(false);
    expect(apiBase()).toBe('/api/v1');
  });

  it('falls back to VITE_GATEWAY_URL when nothing is stored', () => {
    vi.stubEnv('VITE_GATEWAY_URL', 'https://built-in.example.com:8000');
    resetGatewayCache();
    expect(getGatewayBase()).toBe('https://built-in.example.com:8000');
    // …and it is reported as a build default, not a user choice.
    expect(isGatewayUserConfigured()).toBe(false);
  });

  it('lets a stored value outrank the build default', () => {
    vi.stubEnv('VITE_GATEWAY_URL', 'https://built-in.example.com:8000');
    setGatewayBase('https://chosen.example.com');
    expect(getGatewayBase()).toBe('https://chosen.example.com');
  });

  it('notifies subscribers on change', () => {
    const seen: string[] = [];
    const unsubscribe = subscribeGatewayBase(() => seen.push(getGatewayBase()));
    setGatewayBase('https://a.example.com');
    setGatewayBase(null);
    unsubscribe();
    setGatewayBase('https://b.example.com');
    expect(seen).toEqual(['https://a.example.com', '']);
  });
});

describe('describeMixedContent', () => {
  it('says nothing when the page is http', () => {
    expect(describeMixedContent('http://localhost:8000')).toBeNull();
  });

  it('says nothing for an https gateway on an https page', () => {
    setPageProtocol('https:', 'app.example.com');
    expect(describeMixedContent('https://gw.example.com')).toBeNull();
  });

  it('flags http-on-https, noting Chrome’s loopback exemption', () => {
    setPageProtocol('https:', 'app.example.com');
    expect(describeMixedContent('http://localhost:8000')).toMatch(/Chrome allows this/);
  });

  it('flags a non-loopback http gateway as outright blocked', () => {
    setPageProtocol('https:', 'app.example.com');
    expect(describeMixedContent('http://gw.example.com')).toMatch(/block these requests/);
  });
});
