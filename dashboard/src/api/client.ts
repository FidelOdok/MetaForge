import { context as otelContext, propagation } from '@opentelemetry/api';
import axios from 'axios';
import { logger } from '../lib/logger';
import { apiBase } from '../lib/gatewayConfig';

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

// -- Response interceptor: log success/error ------------------------------
apiClient.interceptors.response.use(
  (response) => {
    logger.debug('api_response', {
      status: response.status,
      url: response.config.url,
    });
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

export default apiClient;
