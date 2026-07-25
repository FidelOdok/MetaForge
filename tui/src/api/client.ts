/**
 * Thin HTTP client over the MetaForge gateway. The TUI is a client of the
 * Python platform — no business logic here, just typed calls. Response shapes
 * are hand-declared for now; `npm run gen:types` regenerates the full schema
 * from the gateway's OpenAPI spec so these can be replaced by generated types.
 */
import type { ForgeConfig } from "../config.js";

export interface HealthComponent {
  name: string;
  status: string;
  latency_ms?: number;
  message?: string;
}
export interface Health {
  status: string;
  version?: string;
  uptime_seconds?: number;
  components?: HealthComponent[];
}

export interface WorkProductRef {
  id: string;
  name: string;
  type: string;
  status?: string;
}
export interface Project {
  id: string;
  name: string;
  description?: string;
  status: string;
  work_products?: WorkProductRef[];
}

export interface Run {
  id: string;
  status: string;
  request: Record<string, unknown>;
  approval_reason?: string | null;
  error?: string | null;
  result?: Record<string, unknown> | null;
  history?: string[];
}

export class GatewayError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "GatewayError";
  }
}

export class GatewayClient {
  constructor(private readonly cfg: ForgeConfig) {}

  private base(): string {
    return this.cfg.gateway_url.replace(/\/+$/, "");
  }

  private async get<T>(path: string, timeoutMs = 8000): Promise<T> {
    const res = await fetch(`${this.base()}${path}`, {
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!res.ok) throw new GatewayError(`GET ${path} -> ${res.status}`, res.status);
    return (await res.json()) as T;
  }

  async health(): Promise<Health> {
    return this.get<Health>("/health", 6000);
  }

  async listProjects(): Promise<Project[]> {
    const data = await this.get<{ projects?: Project[] }>("/v1/projects");
    return data.projects ?? [];
  }

  async listRuns(): Promise<Run[]> {
    const data = await this.get<{ runs?: Run[] }>("/v1/runs");
    return data.runs ?? [];
  }
}
