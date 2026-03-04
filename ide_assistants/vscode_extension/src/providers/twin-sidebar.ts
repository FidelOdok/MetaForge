/**
 * TreeDataProvider for the Digital Twin sidebar view.
 *
 * Shows artifacts, constraints, and components fetched from the MetaForge
 * gateway as a tree structure in the VS Code sidebar.
 */

import * as vscode from "vscode";
import type { TwinNode, TwinNodeType } from "../types";
import type { GatewayClient } from "../services/gateway-client";

// ---------------------------------------------------------------------------
// Tree item
// ---------------------------------------------------------------------------

export class TwinTreeItem extends vscode.TreeItem {
  constructor(
    public readonly node: TwinNode,
    collapsible: vscode.TreeItemCollapsibleState
  ) {
    super(node.label, collapsible);

    this.id = node.id;
    this.tooltip = `${node.type}: ${node.label}`;
    this.description = node.type;
    this.contextValue = node.type;
    this.iconPath = TwinTreeItem.iconForType(node.type);
  }

  /**
   * Map twin node types to VS Code ThemeIcon identifiers.
   */
  static iconForType(nodeType: TwinNodeType): vscode.ThemeIcon {
    switch (nodeType) {
      case "artifact":
        return new vscode.ThemeIcon("file-code");
      case "constraint":
        return new vscode.ThemeIcon("shield");
      case "component":
        return new vscode.ThemeIcon("circuit-board");
      case "relationship":
        return new vscode.ThemeIcon("link");
      default:
        return new vscode.ThemeIcon("symbol-misc");
    }
  }
}

// ---------------------------------------------------------------------------
// Tree data provider
// ---------------------------------------------------------------------------

export class TwinSidebarProvider
  implements vscode.TreeDataProvider<TwinTreeItem>
{
  private _onDidChangeTreeData = new vscode.EventEmitter<
    TwinTreeItem | undefined | void
  >();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  private _nodes: TwinNode[] = [];

  constructor(private readonly _client: GatewayClient) {}

  /** Trigger a full refresh of the tree. */
  async refresh(): Promise<void> {
    try {
      const response = await this._client.fetchTwinTree();
      this._nodes = response.nodes;
    } catch {
      this._nodes = [];
      vscode.window.showWarningMessage(
        "MetaForge: Could not fetch Digital Twin data from the gateway."
      );
    }
    this._onDidChangeTreeData.fire();
  }

  // ---- TreeDataProvider interface ----------------------------------------

  getTreeItem(element: TwinTreeItem): vscode.TreeItem {
    return element;
  }

  getChildren(element?: TwinTreeItem): TwinTreeItem[] {
    const sourceNodes = element ? element.node.children ?? [] : this._nodes;

    return sourceNodes.map((node) => {
      const hasChildren = (node.children?.length ?? 0) > 0;
      const state = hasChildren
        ? vscode.TreeItemCollapsibleState.Collapsed
        : vscode.TreeItemCollapsibleState.None;
      return new TwinTreeItem(node, state);
    });
  }

  // ---- Lifecycle ---------------------------------------------------------

  dispose(): void {
    this._onDidChangeTreeData.dispose();
  }
}
