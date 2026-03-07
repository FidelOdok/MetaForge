import axios from 'axios';
import { logger } from '../lib/logger';

/**
 * Base Axios instance for all MetaForge API requests.
 *
 * The Vite dev server proxies `/api` to the Gateway at `http://localhost:8000`,
 * so we only need a relative `baseURL` here.
 */
const apiClient = axios.create({
  baseURL: '/api/v1',
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 30_000,
});

// -- Request interceptor: log + inject W3C trace context ------------------
apiClient.interceptors.request.use((config) => {
  logger.debug('api_request', {
    method: config.method?.toUpperCase(),
    url: config.url,
  });

  // Inject W3C trace-context headers if OTel propagation is available
  try {
    const { propagation, context: otelContext } = require('@opentelemetry/api');
    const carrier: Record<string, string> = {};
    propagation.inject(otelContext.active(), carrier);
    for (const [key, value] of Object.entries(carrier)) {
      if (config.headers) {
        config.headers[key] = value;
      }
    }
  } catch {
    // OTel not available — skip
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
