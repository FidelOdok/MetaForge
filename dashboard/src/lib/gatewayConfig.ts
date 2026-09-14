/**
 * Runtime gateway endpoint resolution (MetaForge Cloud).
 *
 * The dashboard historically hardcoded a *relative* ``/api/v1`` base and
 * relied on something in front of it rewriting that to a real gateway: the
 * Vite dev proxy (``dashboard/vite.config.ts``) in dev, and ``location /api/``
 * in ``nginx.conf`` for the Docker image. On a static host like Vercel there
 * is no such proxy — the browser has to reach the gateway directly, at an
 * address only the operator knows. So the base has to become runtime state.
 *
 * Resolution order:
 *   1. the base the user saved in this browser (``localStorage``)
 *   2. ``VITE_GATEWAY_URL``, baked in at build time
 *   3. ``""`` — same-origin relative paths
 *
 * Step 3 is what keeps ``npm run dev`` and the Docker image working with no
 * configuration at all: an unconfigured gateway resolves to exactly the
 * relative strings this code used before.
 *
 * Note on scheme: an ``https://`` page calling ``http://localhost:8000`` is
 * allowed by Chrome (loopback counts as a potentially-trustworthy origin) but
 * blocked as mixed content by Firefox and Safari. `describeMixedContent`
 * exists so the UI can warn about that rather than leaving the user staring
 * at an opaque network error.
 */

export const GATEWAY_STORAGE_KEY = 'metaforge.gateway.base';

/**
 * The two API prefixes, which are *not* interchangeable.
 *
 * The gateway itself mounts every versioned router at ``/v1`` (see
 * ``APIRouter(prefix="/v1/…")`` across ``api_gateway/``). The extra ``/api``
 * segment exists only as a proxy marker that both front-ends strip on the way
 * through — Vite's ``rewrite: p.replace(/^\/api/, '')`` and nginx's
 * ``proxy_pass http://gateway:8000/``. So a request that goes through a proxy
 * must say ``/api/v1/x`` while the identical request sent straight to the
 * gateway must say ``/v1/x``. Sending ``/api/v1/x`` direct 404s.
 */
export const PROXY_API_PREFIX = '/api/v1';
export const GATEWAY_API_PREFIX = '/v1';

/** Raised when a user-supplied address cannot be turned into a gateway base. */
export class GatewayUrlError extends Error {}

function isLoopback(hostname: string): boolean {
  const h = hostname.toLowerCase().replace(/^\[|\]$/g, '');
  return h === 'localhost' || h === '127.0.0.1' || h === '::1' || h.endsWith('.localhost');
}

function pageIsHttps(): boolean {
  return typeof window !== 'undefined' && window.location?.protocol === 'https:';
}

/**
 * Turn whatever the user typed into a canonical ``scheme://host[:port][/prefix]``
 * with no trailing slash. Accepts bare hosts (``fidel-dev:8000``), full URLs,
 * and URLs carrying a path prefix for gateways behind a reverse proxy.
 *
 * An empty/whitespace input normalizes to ``""``, meaning "use same-origin
 * relative paths" — the proxy behaviour described in the module docstring.
 */
export function normalizeGatewayBase(input: string): string {
  const trimmed = (input ?? '').trim();
  if (!trimmed) return '';

  // A bare host or host:port has no scheme. Pick one that won't immediately
  // trip mixed-content blocking: loopback is always http, everything else
  // inherits the page's scheme so an https dashboard defaults to https.
  let candidate = trimmed;
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(candidate)) {
    const host = candidate.split('/')[0]?.split(':')[0] ?? '';
    candidate = `${!isLoopback(host) && pageIsHttps() ? 'https' : 'http'}://${candidate}`;
  }

  let url: URL;
  try {
    url = new URL(candidate);
  } catch {
    throw new GatewayUrlError(`"${trimmed}" is not a valid address.`);
  }

  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new GatewayUrlError(`Unsupported scheme "${url.protocol}" — use http:// or https://.`);
  }
  if (!url.hostname) {
    throw new GatewayUrlError(`"${trimmed}" is missing a hostname.`);
  }
  if (url.search || url.hash) {
    throw new GatewayUrlError('The gateway address must not contain a query string or fragment.');
  }

  // Keep a path prefix (reverse-proxied gateways), drop a bare trailing slash.
  const prefix = url.pathname.replace(/\/+$/, '');
  // Guard the most likely paste mistake: an API URL rather than the root. Left
  // in, it would silently produce /v1/v1/... and 404 every call.
  const strayApiPath = [PROXY_API_PREFIX, GATEWAY_API_PREFIX, '/api'].find((p) =>
    prefix.endsWith(p),
  );
  if (strayApiPath) {
    throw new GatewayUrlError(
      `Enter the gateway root, not an API path — drop the trailing "${strayApiPath}".`,
    );
  }
  return `${url.protocol}//${url.host}${prefix}`;
}

/** Build-time default, for images that ship pointed at a known gateway. */
function buildTimeDefault(): string {
  const raw = import.meta.env.VITE_GATEWAY_URL;
  if (typeof raw !== 'string' || !raw.trim()) return '';
  try {
    return normalizeGatewayBase(raw);
  } catch {
    return '';
  }
}

/** Split a stored base back into the address/port pair the settings form edits. */
export function splitGatewayBase(base: string): { address: string; port: string } {
  if (!base) return { address: '', port: '' };
  try {
    const url = new URL(base);
    const prefix = url.pathname.replace(/\/+$/, '');
    return {
      address: `${url.protocol}//${url.hostname}${prefix}`,
      port: url.port,
    };
  } catch {
    return { address: base, port: '' };
  }
}

/**
 * Combine the settings form's two fields. An explicit port field wins over a
 * port embedded in the address, since it is the more specific control.
 */
export function joinAddressPort(address: string, port: string): string {
  const addr = (address ?? '').trim();
  const prt = (port ?? '').trim();
  if (!addr) {
    if (!prt) return '';
    throw new GatewayUrlError('Enter an address to go with the port.');
  }

  const base = normalizeGatewayBase(addr);
  if (!prt) return base;

  if (!/^\d+$/.test(prt) || Number(prt) < 1 || Number(prt) > 65535) {
    throw new GatewayUrlError(`"${prt}" is not a valid port (1-65535).`);
  }
  const url = new URL(base);
  url.port = prt;
  return `${url.protocol}//${url.host}${url.pathname.replace(/\/+$/, '')}`;
}

// -- Stored value ----------------------------------------------------------

let cached: string | null = null;

function readStorage(): string {
  if (typeof localStorage === 'undefined') return '';
  try {
    return localStorage.getItem(GATEWAY_STORAGE_KEY) ?? '';
  } catch {
    // Private mode / blocked storage — fall back to the build-time default.
    return '';
  }
}

/** The configured gateway base, or ``""`` when same-origin relative paths apply. */
export function getGatewayBase(): string {
  if (cached === null) {
    const stored = readStorage();
    cached = stored || buildTimeDefault();
  }
  return cached;
}

/** True when the value came from the user rather than the build-time default. */
export function isGatewayUserConfigured(): boolean {
  return readStorage() !== '';
}

const listeners = new Set<() => void>();

/** Persist a new gateway base. Pass ``null`` or ``""`` to clear the override. */
export function setGatewayBase(base: string | null): void {
  const next = base ? normalizeGatewayBase(base) : '';
  try {
    if (next) localStorage.setItem(GATEWAY_STORAGE_KEY, next);
    else localStorage.removeItem(GATEWAY_STORAGE_KEY);
  } catch {
    // Storage unavailable: the in-memory value below still applies for this
    // session, it just won't survive a reload.
  }
  cached = next || buildTimeDefault();
  listeners.forEach((fn) => fn());
}

/** Subscribe to gateway changes, including those made in another tab. */
export function subscribeGatewayBase(fn: () => void): () => void {
  listeners.add(fn);
  const onStorage = (e: StorageEvent) => {
    if (e.key === GATEWAY_STORAGE_KEY) {
      cached = null;
      fn();
    }
  };
  if (typeof window !== 'undefined') window.addEventListener('storage', onStorage);
  return () => {
    listeners.delete(fn);
    if (typeof window !== 'undefined') window.removeEventListener('storage', onStorage);
  };
}

/** Test seam — drops the memoized value so the next read re-reads storage. */
export function resetGatewayCache(): void {
  cached = null;
}

// -- URL builders ----------------------------------------------------------

/**
 * Base for versioned API calls. Direct to a configured gateway that is
 * ``<gateway>/v1``; with none configured it is the proxy-relative ``/api/v1``
 * the dev server and nginx know how to rewrite. See the prefix constants.
 */
export function apiBase(): string {
  const base = getGatewayBase();
  return base ? `${base}${GATEWAY_API_PREFIX}` : PROXY_API_PREFIX;
}

/**
 * Absolute URL for a gateway path that is *not* under ``/api/v1`` — notably
 * ``GET /health``, which ``api_gateway/health.py`` mounts at the bare root.
 */
export function gatewayUrl(path: string): string {
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `${getGatewayBase()}${suffix}`;
}

/** URL for a versioned API path, absolute when a gateway is configured. */
export function apiUrl(path: string): string {
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `${apiBase()}${suffix}`;
}

/**
 * Make a URL the *gateway itself* handed back loadable by the browser.
 *
 * Responses such as ``convert``'s ``glb_url`` are gateway-absolute — they
 * start with ``/v1/`` because that is the gateway's own mount point. Through
 * a proxy the browser has to ask for ``/api/v1/…`` instead; talking directly
 * to a configured gateway it has to ask the gateway's origin. Both fix-ups
 * used to be open-coded as ``` `/api${url}` ``` at five call sites, which
 * silently broke the moment the dashboard was hosted off-origin.
 *
 * Absolute URLs and anything not under ``/v1/`` are returned untouched.
 */
export function resolveGatewayHref(url: string): string {
  if (!url || /^[a-z][a-z0-9+.-]*:/i.test(url) || url.startsWith('//')) return url;
  if (!url.startsWith(`${GATEWAY_API_PREFIX}/`)) return url;
  const base = getGatewayBase();
  return base ? `${base}${url}` : `/api${url}`;
}

/**
 * Explain the mixed-content trap if the given base would hit it, else null.
 * Chrome exempts loopback; Firefox and Safari do not.
 */
export function describeMixedContent(base: string = getGatewayBase()): string | null {
  if (!base || !pageIsHttps()) return null;
  let url: URL;
  try {
    url = new URL(base);
  } catch {
    return null;
  }
  if (url.protocol !== 'http:') return null;
  return isLoopback(url.hostname)
    ? 'This page is served over HTTPS and the gateway over plain HTTP on localhost. Chrome allows this; Firefox and Safari block it as mixed content.'
    : 'This page is served over HTTPS and the gateway over plain HTTP. Browsers will block these requests as mixed content — expose the gateway over HTTPS (Tailscale or a Cloudflare tunnel).';
}
