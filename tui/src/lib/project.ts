/**
 * Chat scope: which project (if any) a chat thread is bound to.
 *
 * Scope is a property of the *thread*, fixed when the gateway creates it
 * (`scope_kind` / `scope_entity_id`). A project-scoped thread gets a project
 * brief prepended to every turn's context — the project's intent and work
 * products, plus the instruction to pass `project_id` when committing CAD or
 * recording a decision (`api_gateway/chat/routes.py::_project_brief`). An
 * assistant-scoped thread gets none of that, so the distinction is load-bearing,
 * not cosmetic: it decides whether new deliverables land in the project.
 *
 * Because of that, the status line must render the scope of the thread that
 * actually exists — never a guess (it used to show whichever project the API
 * happened to list first, which claimed a scope the agent didn't have).
 */
import { randomUUID } from "node:crypto";
import type { Project } from "../api/client.js";

/** No project: a plain assistant conversation. `entityId` is the scope entity. */
export interface AssistantScope {
  kind: "assistant";
  entityId: string;
}
/** Bound to a project — the thread that gets the project brief. */
export interface ProjectScope {
  kind: "project";
  id: string;
  name: string;
}
export type ChatScope = AssistantScope | ProjectScope;

/** A fresh assistant-scoped scope (no project). `prefix` tags the entity id. */
export function assistantScope(prefix = "tui"): AssistantScope {
  return { kind: "assistant", entityId: `${prefix}-${randomUUID().slice(0, 8)}` };
}

/**
 * Stable identity of a scope for effect/dependency comparison. Deliberately
 * ignores an assistant scope's random entity id so re-rendering can't look like
 * a scope change (which would recreate the thread on every frame).
 */
export function scopeKey(scope: ChatScope | null): string | null {
  if (scope === null) return null;
  return scope.kind === "project" ? `project:${scope.id}` : "assistant";
}

/** Status-line label for a scope. `null` = no thread yet. */
export function scopeLabel(scope: ChatScope | null): string {
  if (scope === null) return "no thread";
  return scope.kind === "project" ? scope.name : "no project";
}

/** Words that mean "leave the project" for `/project`. */
const DETACH = new Set(["none", "off", "clear", "-"]);

export function isDetachQuery(query: string): boolean {
  return DETACH.has(query.trim().toLowerCase());
}

export type Resolution = { ok: true; scope: ProjectScope } | { ok: false; error: string };

const asScope = (p: Project): ProjectScope => ({ kind: "project", id: p.id, name: p.name });

/**
 * Resolve a `/project` / `--project` argument against the project list.
 *
 * Match order: exact id → exact name (case-insensitive) → unique substring of a
 * name. Several substring matches is an *error*, not a coin flip — silently
 * picking one would scope the agent's work to the wrong project.
 */
export function resolveProject(projects: Project[], query: string): Resolution {
  const q = query.trim();
  if (!q) return { ok: false, error: "usage: /project <id|name>  (or /project none)" };
  if (!projects.length) return { ok: false, error: "no projects on this gateway" };

  const byId = projects.find((p) => p.id === q);
  if (byId) return { ok: true, scope: asScope(byId) };

  const lower = q.toLowerCase();
  const exact = projects.filter((p) => p.name.toLowerCase() === lower);
  if (exact.length === 1) return { ok: true, scope: asScope(exact[0]!) };
  if (exact.length > 1) return { ok: false, error: ambiguous(q, exact) };

  const partial = projects.filter((p) => p.name.toLowerCase().includes(lower));
  if (partial.length === 1) return { ok: true, scope: asScope(partial[0]!) };
  if (partial.length > 1) return { ok: false, error: ambiguous(q, partial) };

  return { ok: false, error: `no project matches "${q}" — try \`forge projects\`` };
}

function ambiguous(query: string, matches: Project[]): string {
  const shown = matches.slice(0, 5).map((p) => `${p.name} (${p.id.slice(0, 8)})`);
  const more = matches.length > shown.length ? `, +${matches.length - shown.length} more` : "";
  return `"${query}" matches ${matches.length} projects: ${shown.join(", ")}${more} — be more specific or use the id`;
}
