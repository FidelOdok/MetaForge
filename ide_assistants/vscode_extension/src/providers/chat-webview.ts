/**
 * WebviewViewProvider for the MetaForge chat panel.
 *
 * Renders the chat UI inside a VS Code webview and bridges messages
 * between the webview JavaScript context and the extension host.
 */

import * as vscode from "vscode";
import * as path from "path";
import * as fs from "fs";
import type { GatewayClient } from "../services/gateway-client";
import { detectActiveFileContext } from "../services/context-detector";
import type { ChatMessage, ChatScope, ChatThread, WebviewMessage } from "../types";

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export class ChatWebviewProvider implements vscode.WebviewViewProvider {
  public static readonly viewType = "metaforge-chat";

  private _view?: vscode.WebviewView;
  private _disposables: vscode.Disposable[] = [];
  private _currentThread?: ChatThread;

  constructor(
    private readonly _extensionUri: vscode.Uri,
    private readonly _client: GatewayClient
  ) {}

  // ---- WebviewViewProvider interface -------------------------------------

  resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ): void {
    this._view = webviewView;

    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this._extensionUri],
    };

    webviewView.webview.html = this._getHtmlContent(webviewView.webview);

    // Listen for messages from the webview.
    const msgListener = webviewView.webview.onDidReceiveMessage(
      (msg: WebviewMessage) => this._onDidReceiveMessage(msg)
    );
    this._disposables.push(msgListener);

    // Push the initial file context when the view becomes visible.
    webviewView.onDidChangeVisibility(() => {
      if (webviewView.visible) {
        this._pushFileContext();
      }
    });

    // Also detect context on initial resolve.
    this._pushFileContext();

    // Track active editor changes to update context automatically.
    const editorWatcher = vscode.window.onDidChangeActiveTextEditor(() => {
      if (this._view?.visible) {
        this._pushFileContext();
      }
    });
    this._disposables.push(editorWatcher);
  }

  // ---- Public API --------------------------------------------------------

  /** Send a received chat message into the webview for display. */
  postReceiveMessage(message: ChatMessage): void {
    this._postToWebview({ type: "receiveMessage", message });
  }

  /** Update the current thread displayed in the webview. */
  postUpdateThread(thread: ChatThread): void {
    this._currentThread = thread;
    this._postToWebview({ type: "updateThread", thread });
  }

  /** Show or hide the typing indicator. */
  postTyping(agentName: string, isTyping: boolean): void {
    this._postToWebview({ type: "typing", agentName, isTyping });
  }

  // ---- Internal ----------------------------------------------------------

  /**
   * Handle messages coming from the webview.
   */
  private async _onDidReceiveMessage(msg: WebviewMessage): Promise<void> {
    switch (msg.type) {
      case "sendMessage": {
        if (!this._currentThread) {
          vscode.window.showWarningMessage(
            "MetaForge: No active chat thread."
          );
          return;
        }
        try {
          const response = await this._client.sendMessage(
            this._currentThread.id,
            msg.content
          );
          this.postReceiveMessage(response);
        } catch (err) {
          const errMsg =
            err instanceof Error ? err.message : "Unknown error";
          vscode.window.showErrorMessage(
            `MetaForge: Failed to send message — ${errMsg}`
          );
        }
        break;
      }
      default:
        // Other message types are informational / handled elsewhere.
        break;
    }
  }

  /**
   * Detect the active file's context and send a setContext message to the
   * webview so it can discover the relevant thread.
   */
  private _pushFileContext(): void {
    const ctx = detectActiveFileContext();
    if (ctx) {
      this._postToWebview({ type: "setContext", scope: ctx.scope });
    }
  }

  private _postToWebview(message: WebviewMessage): void {
    this._view?.webview.postMessage(message);
  }

  /**
   * Build the HTML content for the chat webview.
   *
   * Tries to load the self-contained `chat-panel.html` from the webview
   * directory. Falls back to a minimal placeholder if the file is missing.
   */
  private _getHtmlContent(_webview: vscode.Webview): string {
    const htmlPath = path.join(
      this._extensionUri.fsPath,
      "src",
      "webview",
      "chat-panel.html"
    );

    try {
      return fs.readFileSync(htmlPath, "utf-8");
    } catch {
      return /* html */ `
        <!DOCTYPE html>
        <html lang="en">
        <head><meta charset="UTF-8" /></head>
        <body>
          <p>MetaForge chat panel could not be loaded.</p>
        </body>
        </html>
      `;
    }
  }

  // ---- Lifecycle ---------------------------------------------------------

  dispose(): void {
    for (const d of this._disposables) {
      d.dispose();
    }
    this._disposables = [];
  }
}
