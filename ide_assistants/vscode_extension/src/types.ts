/**
 * TypeScript interfaces matching MetaForge gateway schemas.
 *
 * These types mirror the API gateway data models so that the VS Code
 * extension can communicate with the gateway in a type-safe manner.
 */

// ---------------------------------------------------------------------------
// Digital Twin types
// ---------------------------------------------------------------------------

/** A node in the Digital Twin graph (work_product, constraint, or component). */
export interface TwinNode {
  id: string;
  type: TwinNodeType;
  label: string;
  metadata: Record<string, unknown>;
  children?: TwinNode[];
}

export type TwinNodeType =
  | "work_product"
  | "constraint"
  | "component"
  | "relationship";

/** Response from the gateway twin tree endpoint. */
export interface TwinTreeResponse {
  nodes: TwinNode[];
  version: string;
}

// ---------------------------------------------------------------------------
// Chat types
// ---------------------------------------------------------------------------

export type ChatActorKind = "user" | "agent" | "system";

export interface ChatActor {
  kind: ChatActorKind;
  displayName: string;
  agentCode?: string;
}

export type ChatScopeKind =
  | "session"
  | "project"
  | "bom-entry"
  | "digital-twin-node";

export interface ChatScope {
  kind: ChatScopeKind;
  entityId?: string;
  label?: string;
}

export interface ChatMessage {
  id: string;
  threadId: string;
  actor: ChatActor;
  content: string;
  createdAt: string;
  status?: "sent" | "sending" | "error";
  graphRef?: ChatGraphRef;
}

export interface ChatGraphRef {
  nodeType: string;
  nodeId: string;
  label: string;
}

export interface ChatThread {
  id: string;
  title: string;
  scope: ChatScope;
  messages: ChatMessage[];
  createdAt: string;
  updatedAt: string;
}

// ---------------------------------------------------------------------------
// Gateway connection
// ---------------------------------------------------------------------------

export interface GatewayConfig {
  baseUrl: string;
}

/** Health check response from the gateway. */
export interface HealthResponse {
  status: "ok" | "degraded" | "down";
  version: string;
}

// ---------------------------------------------------------------------------
// Webview bridge message types
// ---------------------------------------------------------------------------

/**
 * Messages sent between the extension host and the chat webview panel.
 */
export type WebviewMessage =
  | { type: "sendMessage"; content: string }
  | { type: "receiveMessage"; message: ChatMessage }
  | { type: "updateThread"; thread: ChatThread }
  | { type: "setContext"; scope: ChatScope }
  | { type: "typing"; agentName: string; isTyping: boolean };

// ---------------------------------------------------------------------------
// Context detection
// ---------------------------------------------------------------------------

/** Result of detecting the context of the active editor file. */
export interface FileContext {
  scope: ChatScope;
  filePath: string;
}
