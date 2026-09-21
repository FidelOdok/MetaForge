import axios from 'axios';
import type { HealthStatus } from '../../types/health';
import { gatewayUrl } from '../../lib/gatewayConfig';


/** Reject static-host HTML fallbacks instead of reporting a successful connection. */
function parseHealth(data: unknown): HealthStatus {
  if (!data || typeof data !== 'object') throw new Error('This address did not return a MetaForge gateway health response.');
  const value = data as Partial<HealthStatus>;
  if (!['healthy', 'degraded', 'unhealthy'].includes(value.status ?? '') ||
      typeof value.uptime_seconds !== 'number' || typeof value.timestamp !== 'string' ||
      (value.components !== undefined && !Array.isArray(value.components))) {
    throw new Error('This address did not return a valid MetaForge gateway health response.');
  }
  return { ...value, components: value.components ?? [], version: value.version ?? '0.1.0' } as HealthStatus;
}

/**
 * ``GET /health`` lives at the gateway's bare root (``api_gateway/health.py``'s
 * router has no prefix) — unlike everything else under ``/api/v1`` — so this
 * bypasses the shared `apiClient` (whose base is ``…/api/v1``) and builds the
 * URL from the gateway root instead. With no gateway configured that is the
 * root-relative ``/health``, which the dev proxy and prod nginx config both
 * have a matching passthrough entry for.
 */
export async function getHealth(): Promise<HealthStatus> {
  const { data } = await axios.get<HealthStatus>(gatewayUrl('/health'), { timeout: 10_000 });
  return parseHealth(data);
}

/**
 * Probe an *arbitrary* gateway base without disturbing the configured one —
 * what the Settings page's "Test connection" button calls before saving.
 *
 * Resolves with the round-trip latency on success; rejects with a message
 * suitable for display. A cross-origin failure is indistinguishable from a
 * down gateway at the JS level (the browser withholds the detail), so the
 * error text names both possibilities rather than guessing.
 */
export async function probeGateway(
  base: string,
  timeoutMs = 8_000,
): Promise<{ latencyMs: number; status: HealthStatus }> {
  const url = `${base}/health`;
  const started = performance.now();
  try {
    const { data } = await axios.get<HealthStatus>(url, { timeout: timeoutMs });
    return { latencyMs: Math.round(performance.now() - started), status: parseHealth(data) };
  } catch (err) {
    if (axios.isAxiosError(err)) {
      if (err.code === 'ECONNABORTED') {
        throw new Error(`No response within ${Math.round(timeoutMs / 1000)}s — is the gateway running?`);
      }
      if (err.response) {
        throw new Error(`Gateway answered ${err.response.status} ${err.response.statusText} at ${url}`);
      }
      throw new Error(
        `Could not reach ${url}. The gateway may be down, blocked by CORS, or blocked by the browser as mixed content.`,
      );
    }
    throw err instanceof Error ? err : new Error(String(err));
  }
}
