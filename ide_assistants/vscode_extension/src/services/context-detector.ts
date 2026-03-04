/**
 * Detects the active file context and maps it to a ChatScope.
 *
 * File extension mapping:
 *   - *.kicad_sch, *.kicad_pcb         -> scope "bom-entry"
 *   - *.c, *.h, pinmap.json            -> scope "session" (firmware)
 *   - *.FCStd, *.step                  -> scope "digital-twin-node"
 *   - Everything else                  -> scope "project"
 */

import * as vscode from "vscode";
import * as path from "path";
import type { ChatScope, ChatScopeKind, FileContext } from "../types";

// ---------------------------------------------------------------------------
// Extension -> Scope mapping (exported for testability)
// ---------------------------------------------------------------------------

interface ScopeRule {
  /** File extensions (lowercase, with leading dot) or exact basenames. */
  patterns: string[];
  scope: ChatScopeKind;
}

/**
 * Ordered list of rules. First match wins.
 */
export const SCOPE_RULES: ScopeRule[] = [
  {
    patterns: [".kicad_sch", ".kicad_pcb"],
    scope: "bom-entry",
  },
  {
    patterns: [".c", ".h", "pinmap.json"],
    scope: "session",
  },
  {
    patterns: [".fcstd", ".step"],
    scope: "digital-twin-node",
  },
];

/** Default scope when no rule matches. */
export const DEFAULT_SCOPE: ChatScopeKind = "project";

// ---------------------------------------------------------------------------
// Core detection logic (pure function, exported for unit tests)
// ---------------------------------------------------------------------------

/**
 * Determine the chat scope for a given file path.
 *
 * The function checks the file extension and basename against the known
 * rules. It returns the matching scope kind, or "project" as the default.
 */
export function detectScopeFromPath(filePath: string): ChatScopeKind {
  const ext = path.extname(filePath).toLowerCase();
  const basename = path.basename(filePath).toLowerCase();

  for (const rule of SCOPE_RULES) {
    for (const pattern of rule.patterns) {
      // Pattern can be a full basename (e.g. "pinmap.json") or an extension
      if (pattern.startsWith(".")) {
        // Extension match
        if (ext === pattern) {
          return rule.scope;
        }
      } else {
        // Basename match
        if (basename === pattern) {
          return rule.scope;
        }
      }
    }
  }

  return DEFAULT_SCOPE;
}

/**
 * Build a label for the detected scope.
 */
export function buildScopeLabel(
  scopeKind: ChatScopeKind,
  filePath: string
): string {
  const basename = path.basename(filePath);
  switch (scopeKind) {
    case "bom-entry":
      return `BOM: ${basename}`;
    case "session":
      return `Firmware: ${basename}`;
    case "digital-twin-node":
      return `Twin: ${basename}`;
    case "project":
    default:
      return `Project`;
  }
}

// ---------------------------------------------------------------------------
// VS Code integration
// ---------------------------------------------------------------------------

/**
 * Detect the context for the currently active editor.
 *
 * Returns `undefined` if no editor is active.
 */
export function detectActiveFileContext(): FileContext | undefined {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    return undefined;
  }

  const filePath = editor.document.uri.fsPath;
  const scopeKind = detectScopeFromPath(filePath);
  const label = buildScopeLabel(scopeKind, filePath);

  const scope: ChatScope = {
    kind: scopeKind,
    entityId: path.basename(filePath),
    label,
  };

  return { scope, filePath };
}
