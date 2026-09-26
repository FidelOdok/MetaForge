/** Status for a single dependency or the overall system (``DependencyStatus``). */
export type DependencyStatus = 'healthy' | 'degraded' | 'unhealthy';

/** Health snapshot for one downstream dependency (``ComponentHealth``). */
export interface ComponentHealth {
  name: string;
  status: DependencyStatus;
  latency_ms: number | null;
  message: string | null;
}

/** How the gateway authenticates callers (``METAFORGE_AUTH_MODE``). */
export type GatewayAuthMode = 'off' | 'supabase';

/** Response from ``GET /health`` (``HealthResponse``). */
export interface HealthStatus {
  status: DependencyStatus;
  components: ComponentHealth[];
  timestamp: string;
  uptime_seconds: number;
  version: string;

  /**
   * Whether the gateway requires a token, straight from the running process.
   *
   * The dashboard asks rather than assumes, because it is a static app that
   * can be pointed at any gateway: the same build serves a laptop running
   * `auth_mode: off` and a hosted gateway running `supabase`. Optional because
   * a gateway older than this field simply omits it, which reads as `off` —
   * correct, since such a gateway has no authentication to satisfy.
   */
  auth_mode?: GatewayAuthMode;
}
