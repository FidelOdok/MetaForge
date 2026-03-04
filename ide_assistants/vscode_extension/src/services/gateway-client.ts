/**
 * HTTP + WebSocket client for the MetaForge gateway.
 *
 * Reads the gateway URL from the VS Code setting `metaforge.gatewayUrl`
 * (defaults to http://localhost:8000). Provides methods for REST calls
 * to the twin and chat APIs, plus a WebSocket connection for real-time
 * chat streaming.
 */

import * as vscode from "vscode";
import type {
  TwinTreeResponse,
  ChatThread,
  ChatMessage,
  HealthResponse,
  GatewayConfig,
} from "../types";

// ---------------------------------------------------------------------------
// URL helpers (exported for testability)
// ---------------------------------------------------------------------------

/**
 * Build a full API URL from a base URL and a path.
 *
 * Strips trailing slashes from base and ensures a single leading slash on path.
 */
export function buildUrl(base: string, path: string): string {
  const normalizedBase = base.replace(/\/+$/, "");
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${normalizedBase}${normalizedPath}`;
}

/**
 * Derive the WebSocket URL from an HTTP base URL.
 *
 * Replaces `http(s)://` with `ws(s)://`.
 */
export function toWebSocketUrl(httpUrl: string): string {
  return httpUrl.replace(/^http/, "ws");
}

// ---------------------------------------------------------------------------
// GatewayClient
// ---------------------------------------------------------------------------

export class GatewayClient {
  private _config: GatewayConfig;
  private _disposables: vscode.Disposable[] = [];

  constructor() {
    this._config = { baseUrl: GatewayClient.readBaseUrl() };

    // Re-read the base URL whenever the configuration changes.
    const watcher = vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("metaforge.gatewayUrl")) {
        this._config.baseUrl = GatewayClient.readBaseUrl();
      }
    });
    this._disposables.push(watcher);
  }

  /** Read the configured gateway URL from VS Code settings. */
  static readBaseUrl(): string {
    const config = vscode.workspace.getConfiguration("metaforge");
    return config.get<string>("gatewayUrl", "http://localhost:8000");
  }

  get baseUrl(): string {
    return this._config.baseUrl;
  }

  // ---- Health ------------------------------------------------------------

  async checkHealth(): Promise<HealthResponse> {
    const url = buildUrl(this._config.baseUrl, "/api/v1/health");
    const res = await fetch(url);
    if (!res.ok) {
      throw new Error(`Gateway health check failed: ${res.status}`);
    }
    return (await res.json()) as HealthResponse;
  }

  // ---- Digital Twin ------------------------------------------------------

  async fetchTwinTree(): Promise<TwinTreeResponse> {
    const url = buildUrl(this._config.baseUrl, "/api/v1/twin/tree");
    const res = await fetch(url);
    if (!res.ok) {
      throw new Error(`Failed to fetch twin tree: ${res.status}`);
    }
    return (await res.json()) as TwinTreeResponse;
  }

  // ---- Chat threads ------------------------------------------------------

  async listThreads(): Promise<ChatThread[]> {
    const url = buildUrl(this._config.baseUrl, "/api/v1/chat/threads");
    const res = await fetch(url);
    if (!res.ok) {
      throw new Error(`Failed to list threads: ${res.status}`);
    }
    const body = (await res.json()) as { data: ChatThread[] };
    return body.data;
  }

  async getThread(threadId: string): Promise<ChatThread> {
    const url = buildUrl(
      this._config.baseUrl,
      `/api/v1/chat/threads/${threadId}`
    );
    const res = await fetch(url);
    if (!res.ok) {
      throw new Error(`Failed to get thread ${threadId}: ${res.status}`);
    }
    return (await res.json()) as ChatThread;
  }

  async sendMessage(
    threadId: string,
    content: string
  ): Promise<ChatMessage> {
    const url = buildUrl(
      this._config.baseUrl,
      `/api/v1/chat/threads/${threadId}/messages`
    );
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    if (!res.ok) {
      throw new Error(`Failed to send message: ${res.status}`);
    }
    return (await res.json()) as ChatMessage;
  }

  // ---- WebSocket ---------------------------------------------------------

  /**
   * Build the WebSocket URL for a given session.
   */
  getWebSocketUrl(sessionId: string): string {
    const wsBase = toWebSocketUrl(this._config.baseUrl);
    return buildUrl(wsBase, `/api/v1/assistant/ws/${sessionId}`);
  }

  // ---- Lifecycle ---------------------------------------------------------

  dispose(): void {
    for (const d of this._disposables) {
      d.dispose();
    }
    this._disposables = [];
  }
}
