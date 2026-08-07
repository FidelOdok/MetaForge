import axios from 'axios';
import type { HealthStatus } from '../../types/health';

/**
 * ``GET /health`` lives at the gateway's bare root (``api_gateway/health.py``'s
 * router has no prefix) — unlike everything else under ``/api/v1`` — so this
 * bypasses the shared `apiClient` (whose baseURL is ``/api/v1``) and hits the
 * root-relative path directly. The dev proxy and prod nginx config both have
 * a matching ``/health`` passthrough entry.
 */
export async function getHealth(): Promise<HealthStatus> {
  const { data } = await axios.get<HealthStatus>('/health', { timeout: 10_000 });
  return data;
}
