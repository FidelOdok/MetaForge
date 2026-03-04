/**
 * MetaForge VS Code Extension — entry point.
 *
 * Registers the Digital Twin sidebar tree view, the chat webview panel,
 * and the connect/disconnect/refresh commands. Activated on startup.
 */

import * as vscode from "vscode";
import { GatewayClient } from "./services/gateway-client";
import { TwinSidebarProvider } from "./providers/twin-sidebar";
import { ChatWebviewProvider } from "./providers/chat-webview";

// ---------------------------------------------------------------------------
// Activation
// ---------------------------------------------------------------------------

export function activate(context: vscode.ExtensionContext): void {
  const outputChannel = vscode.window.createOutputChannel("MetaForge");
  outputChannel.appendLine("MetaForge extension activating...");

  // ---- Gateway client ----------------------------------------------------
  const client = new GatewayClient();
  context.subscriptions.push({ dispose: () => client.dispose() });

  // ---- Digital Twin sidebar tree -----------------------------------------
  const twinProvider = new TwinSidebarProvider(client);
  const twinView = vscode.window.registerTreeDataProvider(
    "metaforge-twin",
    twinProvider
  );
  context.subscriptions.push(twinView);

  // ---- Chat webview ------------------------------------------------------
  const chatProvider = new ChatWebviewProvider(context.extensionUri, client);
  const chatView = vscode.window.registerWebviewViewProvider(
    ChatWebviewProvider.viewType,
    chatProvider
  );
  context.subscriptions.push(chatView);

  // ---- Commands ----------------------------------------------------------

  context.subscriptions.push(
    vscode.commands.registerCommand("metaforge.refresh", async () => {
      outputChannel.appendLine("Refreshing Digital Twin tree...");
      await twinProvider.refresh();
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("metaforge.connect", async () => {
      try {
        const health = await client.checkHealth();
        vscode.window.showInformationMessage(
          `MetaForge: Connected (${health.status}, v${health.version})`
        );
        outputChannel.appendLine(
          `Connected to gateway: ${client.baseUrl} — status=${health.status}`
        );
        // Auto-refresh the twin tree on successful connection.
        await twinProvider.refresh();
      } catch (err) {
        const errMsg =
          err instanceof Error ? err.message : "Unknown error";
        vscode.window.showErrorMessage(
          `MetaForge: Connection failed — ${errMsg}`
        );
        outputChannel.appendLine(`Connection failed: ${errMsg}`);
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("metaforge.disconnect", () => {
      vscode.window.showInformationMessage(
        "MetaForge: Disconnected from gateway."
      );
      outputChannel.appendLine("Disconnected from gateway.");
    })
  );

  outputChannel.appendLine("MetaForge extension activated.");
}

// ---------------------------------------------------------------------------
// Deactivation
// ---------------------------------------------------------------------------

export function deactivate(): void {
  // Disposables registered via context.subscriptions are cleaned up
  // automatically by VS Code. Nothing extra needed here.
}
