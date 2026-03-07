/**
 * OpenTelemetry browser tracing initialisation.
 *
 * Sets up a WebTracerProvider that exports spans via OTLP/HTTP to the
 * `/otlp/v1/traces` endpoint (proxied by nginx to the OTel Collector).
 * Automatically instruments `fetch` and `XMLHttpRequest` for any request
 * targeting `/api/`.
 *
 * Wrapped in try/catch so the dashboard degrades gracefully if the OTel
 * packages fail to load.
 */

export function initTelemetry(): void {
  try {
    /* eslint-disable @typescript-eslint/no-require-imports */
    const { WebTracerProvider } = require('@opentelemetry/sdk-trace-web');
    const { BatchSpanProcessor } = require('@opentelemetry/sdk-trace-base');
    const {
      OTLPTraceExporter,
    } = require('@opentelemetry/exporter-trace-otlp-http');
    const {
      FetchInstrumentation,
    } = require('@opentelemetry/instrumentation-fetch');
    const {
      XMLHttpRequestInstrumentation,
    } = require('@opentelemetry/instrumentation-xml-http-request');
    const { ZoneContextManager } = require('@opentelemetry/context-zone');
    const { Resource } = require('@opentelemetry/resources');
    const {
      ATTR_SERVICE_NAME,
    } = require('@opentelemetry/semantic-conventions');
    const { registerInstrumentations } = require('@opentelemetry/instrumentation');
    /* eslint-enable @typescript-eslint/no-require-imports */

    const resource = new Resource({
      [ATTR_SERVICE_NAME]: 'metaforge-dashboard',
    });

    const exporter = new OTLPTraceExporter({
      url: '/otlp/v1/traces',
    });

    const provider = new WebTracerProvider({
      resource,
      spanProcessors: [new BatchSpanProcessor(exporter)],
    });

    provider.register({
      contextManager: new ZoneContextManager(),
    });

    registerInstrumentations({
      instrumentations: [
        new FetchInstrumentation({
          propagateTraceHeaderCorsUrls: [/\/api\//],
        }),
        new XMLHttpRequestInstrumentation({
          propagateTraceHeaderCorsUrls: [/\/api\//],
        }),
      ],
    });

    console.info('[telemetry] OpenTelemetry initialised');
  } catch (err) {
    console.warn('[telemetry] Failed to initialise OpenTelemetry:', err);
  }
}
