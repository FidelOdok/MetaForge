/** Status for a single dependency or the overall system (``DependencyStatus``). */
export type DependencyStatus = 'healthy' | 'degraded' | 'unhealthy';

/** Health snapshot for one downstream dependency (``ComponentHealth``). */
export interface ComponentHealth {
  name: string;
  status: DependencyStatus;
  latency_ms: number | null;
  message: string | null;
}

/** Response from ``GET /health`` (``HealthResponse``). */
export interface HealthStatus {
  status: DependencyStatus;
  components: ComponentHealth[];
  timestamp: string;
  uptime_seconds: number;
  version: string;
}
