/**
 * Chat SSE client for /v1/chat. Mirrors the gateway's event contract
 * (api_gateway/chat/streaming.py): message.delta / agent.step / agent.done /
 * error. `streamThread` yields parsed events off the fetch body stream.
 */

import { log } from "../log.js";

export interface AgentStep {
  index?: number;
  thought?: string;
  tool?: string;
  arguments?: unknown;
  observation?: unknown;
  error?: string;
  final?: unknown;
}

export type ChatEvent =
  | { type: "message.delta"; delta: string }
  | { type: "agent.step"; step: AgentStep }
  | { type: "agent.done" }
  | { type: "error"; error: string }
  | { type: "other"; event: string; data: unknown };

export function parseEvent(raw: string): ChatEvent | null {
  let event = "message";
  let dataStr = "";
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataStr += line.slice(5).trim();
  }
  if (!dataStr) return null;
  let envelope: Record<string, unknown>;
  try {
    envelope = JSON.parse(dataStr) as Record<string, unknown>;
  } catch {
    return null;
  }
  // The gateway wraps every event payload in a `data` envelope alongside
  // `thread_id`/`timestamp` (api_gateway/chat/streaming.py), so the fields we
  // want (delta/step/error) live under `envelope.data`, not at the top level.
  // Unwrap it; fall back to the envelope itself for any un-enveloped event.
  const data = (
    envelope.data && typeof envelope.data === "object" ? envelope.data : envelope
  ) as Record<string, unknown>;
  switch (event) {
    case "message.delta":
      return { type: "message.delta", delta: String(data.delta ?? "") };
    case "agent.step":
      return { type: "agent.step", step: (data.step as AgentStep) ?? {} };
    case "agent.done":
      return { type: "agent.done" };
    case "error":
      return { type: "error", error: String(data.error ?? "unknown error") };
    default:
      return { type: "other", event, data };
  }
}

/** Open the thread SSE stream and yield parsed chat events until aborted/closed.
 *
 * ``onOpen`` fires once the connection is established (used to clear a
 * "reconnecting" state and reset backoff). A thrown error or a clean end lets
 * the caller reconnect — the thread lives server-side, so we just reattach. */
export async function* streamThread(
  base: string,
  threadId: string,
  signal: AbortSignal,
  onOpen?: () => void,
): AsyncGenerator<ChatEvent> {
  const res = await fetch(`${base}/v1/chat/threads/${threadId}/stream`, {
    signal,
    headers: { Accept: "text/event-stream" },
  });
  if (!res.ok || !res.body) throw new Error(`chat stream -> ${res.status}`);
  onOpen?.();

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buf.indexOf("\n\n")) !== -1) {
      const raw = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      log.debug("sse.frame", { raw });
      const ev = parseEvent(raw);
      // A delta event that parsed to no text is the fingerprint of an SSE
      // payload/parse mismatch — surface it loudly rather than silently drop it.
      if (ev?.type === "message.delta" && ev.delta === "") log.warn("sse.empty_delta", { raw });
      if (ev) yield ev;
    }
  }
}
