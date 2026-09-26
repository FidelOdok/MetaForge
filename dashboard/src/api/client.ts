import { context as otelContext, propagation } from '@opentelemetry/api';
import axios from 'axios';
import { getAccessToken } from '../auth/accessToken';
import { logger } from '../lib/logger';
import { apiBase, getGatewayBase } from '../lib/gatewayConfig';
import { installSampleAdapter } from '../lib/sample-workspace';

/**
 * Base Axios instance for all MetaForge API requests.
 *
 * `baseURL` is deliberately *not* set here. It used to be the constant
 * `/api/v1`, which worked only because something in front of the app rewrote
 * it: the Vite dev proxy in dev, `location /api/` in `nginx.conf` for the
 * Docker image. A statically hosted dashboard (Vercel) has no such proxy and
 * must call whatever gateway the user configured, so the base is resolved per
 * request in the interceptor below. With no gateway configured `apiBase()`
 * returns the original relative `/api/v1`, leaving dev and Docker unchanged.
 */
const apiClient = axios.create({
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 30_000,
});

// -- Request interceptor: resolve base + log + inject W3C trace context ----
apiClient.interceptors.request.use((config) => {
  // Resolved per request, not at module load: the user can change the gateway
  // from Settings at any time and in-flight-after callers must see it.
  config.baseURL = apiBase();

  // Bearer token for gateways running `auth_mode: supabase`. Read from a
  // module-level holder that AuthProvider keeps in step with the session,
  // including Supabase's silent refreshes — an async lookup here would put a
  // promise in front of every request in the app.
  //
  // Null on a local gateway, which wants no Authorization header at all, so
  // the header is omitted rather than sent empty.
  const token = getAccessToken();
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`;
  }

  logger.debug('api_request', {
    method: config.method?.toUpperCase(),
    url: config.url,
  });

  // Inject W3C trace-context headers if OTel propagation is available
  // MET-736: this was a `require('@opentelemetry/api')` inside a try/catch.
  // `require` does not exist in a browser ESM module and Vite does not shim
  // it for app code -- it survived verbatim into the bundle, threw
  // ReferenceError on this line, and the catch swallowed it. So no
  // `traceparent` header was ever sent and browser spans never linked to
  // gateway traces, silently, since this was written.
  //
  // The guard was never needed either: @opentelemetry/api is a hard
  // dependency of this package, not an optional one.
  const carrier: Record<string, string> = {};
  propagation.inject(otelContext.active(), carrier);
  for (const [key, value] of Object.entries(carrier)) {
    if (config.headers) {
      config.headers[key] = value;
    }
  }

  return config;
});

/**
 * A 200 carrying HTML is never a valid API response — it means something in
 * front of the gateway answered instead of the gateway.
 *
 * This is how `app.metaforge.uk` came up broken: the Vercel SPA catch-all
 * rewrite matched `/api/v1/projects` and served `index.html` with a 200, so
 * axios handed the page its own shell as JSON, `data.projects` was undefined,
 * and `.map` threw into the ErrorBoundary. The user saw "Something went
 * wrong", which named neither the cause nor the fix.
 *
 * The rewrite is corrected in `vercel.json`, but that only fixes one host.
 * Any reverse proxy, captive portal or tunnel can do the same thing, so the
 * client refuses the response here too, with a message that says what to do.
 */
function rejectIfHtml(response: { status: number; headers: unknown; config: { url?: string } }) {
  const headers = response.headers as { 'content-type'?: string } | undefined;
  const contentType = headers?.['content-type'] ?? '';
  if (!contentType.includes('text/html')) return;

  const base = getGatewayBase();
  throw new Error(
    base
      ? `The gateway at ${base} returned an HTML page instead of data. That address is ` +
        `probably serving a website rather than a MetaForge gateway — check it in Settings.`
      : 'No gateway is configured, so this request was answered by the dashboard itself. ' +
        'Set your gateway address in Settings → Gateway.',
  );
}

// -- Response interceptor: log success/error ------------------------------
apiClient.interceptors.response.use(
  (response) => {
    logger.debug('api_response', {
      status: response.status,
      url: response.config.url,
    });
    rejectIfHtml(response);
    return response;
  },
  (error) => {
    logger.error('api_error', {
      status: error.response?.status,
      url: error.config?.url,
      message: error.message,
    });
    return Promise.reject(error);
  },
);

// In sample mode (`?demo=1`) every request is answered by the in-memory sample
// workspace instead of the network, so all pages work offline.
installSampleAdapter(apiClient);

export default apiClient;
