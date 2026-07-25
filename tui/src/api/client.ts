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

export interface TwinNode {
  id: string;
  name: string;
  type: string;
  domain?: string;
  status?: string;
  content_hash?: string;
  properties?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
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

  baseUrl(): string {
    return this.base();
  }

  private async post<T>(path: string, body: unknown, timeoutMs = 15000): Promise<T> {
    const res = await fetch(`${this.base()}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!res.ok) throw new GatewayError(`POST ${path} -> ${res.status}`, res.status);
    return (await res.json()) as T;
  }

  /** Create a run (design flow when the request carries a `flow`). */
  async createRun(request: Record<string, unknown>, start = true): Promise<Run> {
    return this.post<Run>("/v1/runs", { request, start });
  }

  async getRun(id: string): Promise<Run> {
    return this.get<Run>(`/v1/runs/${id}`);
  }

  /** Fetch a twin node (work product) with its properties. */
  async getNode(id: string): Promise<TwinNode> {
    const data = await this.get<TwinNode & { node?: TwinNode }>(`/v1/twin/nodes/${id}`);
    return data.node ?? data;
  }

  /** Approve or reject a run paused at a gate. */
  async submitApproval(id: string, decision: "approve" | "reject"): Promise<Run> {
    return this.post<Run>(`/v1/runs/${id}/approval`, { decision });
  }

  /** Create an assistant-scoped chat thread. */
  async createThread(scopeEntityId: string, title?: string): Promise<{ id: string }> {
    return this.post<{ id: string }>("/v1/chat/threads", {
      scope_kind: "assistant",
      scope_entity_id: scopeEntityId,
      title,
    });
  }

  /**
   * Post a user message. The gateway runs the agent turn inside this request
   * while the SSE stream delivers deltas/steps concurrently, so callers fire
   * this without blocking the UI on the returned envelope.
   */
  async sendMessage(
    threadId: string,
    content: string,
    opts: { provider?: string; model?: string } = {},
  ): Promise<void> {
    await this.post<unknown>(
      `/v1/chat/threads/${threadId}/messages`,
      {
        content,
        actor_id: "tui-user",
        actor_kind: "user",
        provider: opts.provider,
        model: opts.model,
      },
      120000,
    );
  }
}
