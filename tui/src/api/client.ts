/**
 * Thin HTTP client over the MetaForge gateway. The TUI is a client of the
 * Python platform — no business logic here, just typed calls. Response shapes
 * are hand-declared for now; `npm run gen:types` regenerates the full schema
 * from the gateway's OpenAPI spec so these can be replaced by generated types.
 */
import { Agent } from "undici";
import type { ForgeConfig } from "../config.js";
// Type-only (erased at compile), so this stays a one-way runtime dependency:
// lib/project.ts is what owns the scope model.
import type { ChatScope } from "../lib/project.js";

/**
 * A chat turn's POST sends no response headers until the whole handler
 * settles (the harness can run many tool calls — a CAD build takes minutes),
 * so it needs a long ceiling. `AbortSignal.timeout` alone isn't enough:
 * Node's global `fetch` is backed by undici, whose default Agent applies its
 * own `headersTimeout`/`bodyTimeout` (~300s) independently of any abort
 * signal the caller sets, cutting a real multi-minute turn off early even
 * though `sendMessage` already asks for up to 600000ms below. Live-caught via
 * a decoupling test: a direct `curl -m 900` to the same endpoint completed in
 * 253-488s while this client aborted every time with "The operation timed
 * out." Passing this dispatcher only to the long-running turn POST keeps
 * every other call (health, projects, runs) on the default Agent.
 */
// MET-590: with the gateway streaming agent.step events live, the real
// liveness guard is the idle watchdog in useChat (abort when no progress
// events arrive for a while). This is only the absolute hard ceiling so a
// dead watchdog can't leak a request forever.
const LONG_TURN_TIMEOUT_MS = 2_700_000; // 45 min
const longTurnDispatcher = new Agent({
  headersTimeout: LONG_TURN_TIMEOUT_MS + 30000,
  bodyTimeout: LONG_TURN_TIMEOUT_MS + 30000,
});

/**
 * MET-610: the shipped binary is compiled with Bun, whose fetch ignores the
 * undici `dispatcher` above AND applies its own 5-minute *idle* timeout
 * (aborting with "The operation timed out." once no response bytes arrive
 * for 300s — which is what every long turn POST looks like, since the
 * gateway sends nothing until the turn settles). `timeout: false` is Bun's
 * escape hatch; Node's fetch ignores the unknown option, so both runtimes
 * end up with only the intended guards (idle watchdog + 45-min ceiling).
 */
export const NO_RUNTIME_IDLE_TIMEOUT = { timeout: false } as const;

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

export interface ThreadSummary {
  id: string;
  scope_kind: string;
  scope_entity_id: string;
  title: string;
  archived: boolean;
  last_message_at?: string;
  message_count?: number;
}

export interface ThreadDetail {
  id: string;
  scope_kind?: string;
  scope_entity_id?: string;
  title?: string;
  messages?: ThreadMessage[];
}

export interface ThreadMessage {
  role?: string;
  actor_kind?: string;
  content?: string;
  text?: string;
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

export interface HarnessProvider {
  id: string;
  family: string;
  configured: boolean;
  base_url?: string | null;
}
export interface HarnessProvidersResponse {
  active_provider?: string | null;
  active_model?: string | null;
  providers: HarnessProvider[];
}

export class GatewayClient {
  constructor(private readonly cfg: ForgeConfig) {}

  private base(): string {
    return this.cfg.gateway_url.replace(/\/+$/, "");
  }

  /** Credential-write admin token (sent when the gateway requires one). */
  private adminHeaders(): Record<string, string> {
    const t = (process.env.METAFORGE_HARNESS_ADMIN_TOKEN ?? "").trim();
    return t ? { "X-MetaForge-Admin": t } : {};
  }

  private async send<T>(
    method: "POST" | "PUT" | "DELETE",
    path: string,
    body?: unknown,
    timeoutMs = 15000,
  ): Promise<T> {
    const res = await fetch(`${this.base()}${path}`, {
      method,
      headers: { "Content-Type": "application/json", ...this.adminHeaders() },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (res.status === 401) {
      throw new GatewayError(
        `${method} ${path} -> 401 (set METAFORGE_HARNESS_ADMIN_TOKEN to authorize)`,
        401,
      );
    }
    if (!res.ok) {
      // Surface FastAPI's `detail` when present — a write that fails (e.g.
      // couldn't persist a credential) must say why, not just a status code.
      let detail: string | undefined;
      try {
        const body = (await res.clone().json()) as { detail?: string };
        detail = body?.detail;
      } catch {
        /* body wasn't JSON — fall back to the bare status below */
      }
      throw new GatewayError(detail ?? `${method} ${path} -> ${res.status}`, res.status);
    }
    return (await res.json()) as T;
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

  async listTwinNodes(): Promise<TwinNode[]> {
    const data = await this.get<{ nodes?: TwinNode[] }>("/v1/twin/nodes");
    return data.nodes ?? [];
  }

  async getThread(id: string): Promise<ThreadDetail> {
    return this.get<ThreadDetail>(`/v1/chat/threads/${id}`);
  }

  /** Recent chat threads, newest activity first (MET-595 resume picker). */
  async listThreads(perPage = 20): Promise<ThreadSummary[]> {
    const d = await this.get<{ threads?: ThreadSummary[] }>(
      `/v1/chat/threads?per_page=${perPage}`,
    );
    const threads = d.threads ?? [];
    return threads.sort((a, b) =>
      String(b.last_message_at ?? "").localeCompare(String(a.last_message_at ?? "")),
    );
  }

  async listSources(): Promise<Array<Record<string, unknown>>> {
    const d = await this.get<{ sources?: Array<Record<string, unknown>> }>("/v1/knowledge/sources");
    return d.sources ?? [];
  }

  async memoryRetrieve(goal: string, limit = 5): Promise<Array<Record<string, unknown>>> {
    const d = await this.post<{ hits?: Array<Record<string, unknown>> }>("/v1/memory/retrieve", {
      goal,
      limit,
    });
    return d.hits ?? [];
  }

  async listProposals(): Promise<Array<Record<string, unknown>>> {
    const d = await this.get<{ proposals?: Array<Record<string, unknown>> }>(
      "/v1/assistant/proposals",
    );
    return d.proposals ?? [];
  }

  async decideProposal(
    changeId: string,
    decision: "approve" | "reject",
    reason?: string,
  ): Promise<unknown> {
    return this.post(`/v1/assistant/proposals/${changeId}/decide`, { decision, reason });
  }

  baseUrl(): string {
    return this.base();
  }

  // ---- provider auth (forge auth) ----

  async listHarnessProviders(): Promise<HarnessProvidersResponse> {
    return this.get<HarnessProvidersResponse>("/v1/harness/providers");
  }

  /** Store a provider credential on the gateway. api_key OR an oauth token blob. */
  async setCredential(body: {
    provider: string;
    method: "api_key" | "oauth";
    api_key?: string;
    base_url?: string;
    tokens?: Record<string, unknown>;
  }): Promise<unknown> {
    return this.send("POST", "/v1/harness/credentials", body);
  }

  async setSelection(provider: string, model?: string): Promise<unknown> {
    return this.send("PUT", "/v1/harness/selection", { provider, model });
  }

  async deleteCredential(provider: string): Promise<unknown> {
    return this.send("DELETE", `/v1/harness/credentials/${provider}`);
  }

  private async post<T>(
    path: string,
    body: unknown,
    timeoutMs = 15000,
    dispatcher?: Agent,
    signal?: AbortSignal,
  ): Promise<T> {
    const timeout = AbortSignal.timeout(timeoutMs);
    const res = await fetch(`${this.base()}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      // The caller's signal (idle watchdog) composes with the hard ceiling.
      signal: signal ? AbortSignal.any([timeout, signal]) : timeout,
      // The dispatcher raises undici's timeouts (Node); NO_RUNTIME_IDLE_TIMEOUT
      // disables Bun's 5-min idle timeout (the compiled binary). MET-610.
      ...(dispatcher ? { dispatcher, ...NO_RUNTIME_IDLE_TIMEOUT } : {}),
    } as RequestInit);
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

  /**
   * Create a chat thread in the given scope.
   *
   * A `project` scope is what makes the gateway lead every turn with the
   * project brief (its work products, and the instruction to persist new CAD /
   * decisions into that project), so the scope has to be chosen here rather
   * than hardcoded — it used to always be `assistant`, which made a
   * project-scoped conversation impossible from this client.
   */
  async createThread(scope: ChatScope, title?: string): Promise<{ id: string }> {
    return this.post<{ id: string }>("/v1/chat/threads", {
      scope_kind: scope.kind,
      scope_entity_id: scope.kind === "project" ? scope.id : scope.entityId,
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
    opts: { provider?: string; model?: string; signal?: AbortSignal } = {},
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
      // A turn runs synchronously inside this POST (the harness can make many
      // tool calls — a CAD build takes minutes), so allow a long ceiling. The
      // turn is finalized on this POST resolving; a client abort here would
      // finalize prematurely, so keep it well above realistic turn times.
      LONG_TURN_TIMEOUT_MS,
      longTurnDispatcher,
      opts.signal,
    );
  }
}
