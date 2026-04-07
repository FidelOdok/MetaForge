/**
 * Grafana Faro browser observability initialisation.
 *
 * Ships browser logs (console.log/warn/error), unhandled JS errors, and
 * web vitals to Grafana Loki via the Alloy faro.receiver endpoint.
 *
 * The Vite dev server proxies /faro → http://alloy:12347 so CORS is not
 * an issue. In production the nginx reverse proxy handles the same route.
 *
 * Initialise before React mounts so errors during render are captured.
 */

import {
  initializeFaro,
  getWebInstrumentations,
  LogLevel,
  type Faro,
} from '@grafana/faro-web-sdk';

let faro: Faro | null = null;

export function initFaro(): void {
  try {
    faro = initializeFaro({
      url: '/faro/collect',
      app: {
        name: 'metaforge-dashboard',
        version: '0.1.0',
        environment: import.meta.env.MODE,
      },
      instrumentations: [
        ...getWebInstrumentations({
          captureConsole: true,
          // Skip debug — too noisy in dev; keep info/warn/error
          captureConsoleDisabledLevels: [LogLevel.DEBUG, LogLevel.TRACE],
        }),
      ],
    });

    console.info('[faro] Grafana Faro initialised');
  } catch (err) {
    // Faro failure must never break the app
    console.warn('[faro] Failed to initialise Faro:', err);
  }
}

/** Access the Faro instance for manual event pushing (optional). */
export function getFaro(): Faro | null {
  return faro;
}
