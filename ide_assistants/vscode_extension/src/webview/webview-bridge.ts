/**
 * Webview bridge — handles message passing between the chat webview and the
 * extension host.
 *
 * This module centralises the logic for:
 *  - Receiving messages from the webview (onDidReceiveMessage)
 *  - Sending messages to the webview (postMessage)
 *  - Thread discovery based on current file context
 *
 * Message types:
 *  - sendMessage     : User sends a chat message (webview -> host)
 *  - receiveMessage  : Agent reply delivered to webview (host -> webview)
 *  - updateThread    : Full thread state pushed to webview (host -> webview)
 *  - setContext       : File context scope update (host -> webview)
 *  - typing           : Typing indicator toggle (host -> webview)
 */

import * as vscode from "vscode";
import type { GatewayClient } from "../services/gateway-client";
import { detectActiveFileContext } from "../services/context-detector";
import type {
  ChatMessage,
  ChatScope,
  ChatThread,
  WebviewMessage,
} from "../types";

// ---------------------------------------------------------------------------
// WebviewBridge
// ---------------------------------------------------------------------------

export class WebviewBridge {
  private _disposables: vscode.Disposable[] = [];
  private _currentThread?: ChatThread;
  private _currentScope?: ChatScope;

  constructor(
    private readonly _webview: vscode.Webview,
    private readonly _client: GatewayClient
  ) {
    // Subscribe to messages from the webview.
    const listener = this._webview.onDidReceiveMessage(
      (msg: WebviewMessage) => this._handleMessage(msg)
    );
    this._disposables.push(listener);
  }

  // ---- Public API --------------------------------------------------------

  /** Push a received message into the webview for display. */
  postReceiveMessage(message: ChatMessage): void {
    this._post({ type: "receiveMessage", message });
  }

  /** Replace the entire thread state in the webview. */
  postUpdateThread(thread: ChatThread): void {
    this._currentThread = thread;
    this._post({ type: "updateThread", thread });
  }

  /** Update the scope label shown in the webview header. */
  postSetContext(scope: ChatScope): void {
    this._currentScope = scope;
    this._post({ type: "setContext", scope });
  }

  /** Show or hide the typing indicator. */
  postTyping(agentName: string, isTyping: boolean): void {
    this._post({ type: "typing", agentName, isTyping });
  }

  /** Detect the current file context and push it to the webview. */
  detectAndPushContext(): void {
    const ctx = detectActiveFileContext();
    if (ctx) {
      this.postSetContext(ctx.scope);
    }
  }

  /**
   * Discover the appropriate thread for the current scope.
   *
   * Attempts to find an existing thread whose scope matches; if none is
   * found, the caller may decide to create one.
   */
  async discoverThread(): Promise<ChatThread | undefined> {
    try {
      const threads = await this._client.listThreads();
      if (!this._currentScope) {
        return threads[0];
      }
      // Find a thread whose scope kind matches the detected context.
      const match = threads.find(
        (t) => t.scope.kind === this._currentScope!.kind
      );
      return match ?? threads[0];
    } catch {
      return undefined;
    }
  }

  get currentThread(): ChatThread | undefined {
    return this._currentThread;
  }

  get currentScope(): ChatScope | undefined {
    return this._currentScope;
  }

  // ---- Internal ----------------------------------------------------------

  private async _handleMessage(msg: WebviewMessage): Promise<void> {
    switch (msg.type) {
      case "sendMessage": {
        if (!this._currentThread) {
          return;
        }
        try {
          const reply = await this._client.sendMessage(
            this._currentThread.id,
            msg.content
          );
          this.postReceiveMessage(reply);
        } catch {
          // Error is surfaced by the provider level.
        }
        break;
      }
      default:
        break;
    }
  }

  private _post(message: WebviewMessage): void {
    this._webview.postMessage(message);
  }

  // ---- Lifecycle ---------------------------------------------------------

  dispose(): void {
    for (const d of this._disposables) {
      d.dispose();
    }
    this._disposables = [];
  }
}

// ---------------------------------------------------------------------------
// Supported message types (re-exported for reference)
// ---------------------------------------------------------------------------

export const MESSAGE_TYPES = [
  "sendMessage",
  "receiveMessage",
  "updateThread",
  "setContext",
  "typing",
] as const;

export type MessageType = (typeof MESSAGE_TYPES)[number];
