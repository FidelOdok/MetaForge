/**
 * Session-resume helpers (MET-595) — pure, unit-tested.
 *
 * The server is the source of truth for a thread's history; resuming is
 * attaching to an existing thread id and rendering its persisted messages
 * back into the transcript. Context is rebuilt server-side per turn, so a
 * resumed conversation continues with full (token-budgeted) memory.
 */

import type { ThreadMessage, ThreadSummary } from "../api/client.js";
import type { ChatMessage } from "../hooks/useChat.js";

/** Persisted thread messages -> transcript entries. System/error rows are
 * dropped (they were transient run artifacts, not conversation). */
export function backfillMessages(messages: ThreadMessage[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  for (const m of messages) {
    const kind = m.actor_kind ?? m.role;
    const text = String(m.content ?? m.text ?? "");
    if (!text) continue;
    if (kind === "user") out.push({ role: "user", text });
    else if (kind === "agent" || kind === "assistant") out.push({ role: "assistant", text });
  }
  return out;
}

/** Compact "2h ago"-style timestamp for the picker. */
export function relativeTime(iso: string | undefined, now: number = Date.now()): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.floor((now - t) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** One picker row: title · scope · activity. */
export function describeThread(t: ThreadSummary, now: number = Date.now()): string {
  const scope =
    t.scope_kind === "project" ? `project ${t.scope_entity_id.slice(0, 8)}` : "assistant";
  const count = t.message_count ? `${t.message_count} msg` : "empty";
  const when = relativeTime(t.last_message_at, now);
  return `${t.title || "(untitled)"} · ${scope} · ${count}${when ? ` · ${when}` : ""}`;
}

/** Picker candidates: unarchived, non-empty, excluding the current thread. */
export function pickerCandidates(
  threads: ThreadSummary[],
  currentThreadId: string | null,
  limit = 10,
): ThreadSummary[] {
  return threads
    .filter((t) => !t.archived && t.id !== currentThreadId && (t.message_count ?? 0) > 0)
    .slice(0, limit);
}
