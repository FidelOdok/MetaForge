/** Actor kinds in the chat system */
export type ChatActorKind = 'user' | 'agent' | 'system';

/** Actor identity in a chat message */
export interface ChatActor {
  id: string;
  kind: ChatActorKind;
  displayName: string;
  /** Agent code (e.g., 'ME', 'EE', 'FW') — present only when kind === 'agent' */
  agentCode?: string;
}

/** Message delivery status */
export type MessageStatus = 'sending' | 'sent' | 'delivered' | 'error';

/** Reference to a graph entity (Digital Twin node) */
export interface ChatGraphRef {
  nodeId: string;
  nodeType: string;
  label: string;
}

/** A single chat message */
export interface ChatMessage {
  id: string;
  threadId: string;
  actor: ChatActor;
  content: string;
  status: MessageStatus;
  createdAt: string;
  updatedAt?: string;
  graphRef?: ChatGraphRef;
}

/** Scope kinds for chat threads */
export type ChatScopeKind = 'session' | 'approval' | 'bom-entry' | 'digital-twin-node' | 'project' | 'assistant';

/** Scope that a chat thread is bound to */
export interface ChatScope {
  kind: ChatScopeKind;
  entityId: string;
  label?: string;
}

/** Chat thread (conversation) */
export interface ChatThread {
  id: string;
  scope: ChatScope;
  channelId: string;
  title: string;
  messages: ChatMessage[];
  participants: ChatActor[];
  createdAt: string;
  lastMessageAt: string;
  archived: boolean;
}

/** Chat channel (grouping for threads) */
export interface ChatChannel {
  id: string;
  name: string;
  scopeKind: ChatScopeKind;
  unreadCount: number;
}

// --- WebSocket event payloads ---

/** New message created event */
export interface ChatMessageCreatedEvent {
  type: 'chat.message.sent';
  threadId: string;
  message: ChatMessage;
}

/** Streaming message chunk event */
export interface ChatMessageChunkEvent {
  type: 'chat.message.chunk';
  threadId: string;
  messageId: string;
  chunk: string;
  done: boolean;
}

/** Agent typing indicator event */
export interface ChatAgentTypingEvent {
  type: 'chat.agent.typing';
  threadId: string;
  actor: ChatActor;
  isTyping: boolean;
}

/** Union of all chat WebSocket events */
export type ChatEvent =
  | ChatMessageCreatedEvent
  | ChatMessageChunkEvent
  | ChatAgentTypingEvent;
