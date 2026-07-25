/**
 * Chat SSE client for /v1/chat. Mirrors the gateway's event contract
 * (api_gateway/chat/streaming.py): message.delta / agent.step / agent.done /
 * error. `streamThread` yields parsed events off the fetch body stream.
 */

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
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(dataStr) as Record<string, unknown>;
  } catch {
    return null;
  }
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

/** Open the thread SSE stream and yield parsed chat events until aborted/closed. */
export async function* streamThread(
  base: string,
  threadId: string,
  signal: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const res = await fetch(`${base}/v1/chat/threads/${threadId}/stream`, {
    signal,
    headers: { Accept: "text/event-stream" },
  });
  if (!res.ok || !res.body) throw new Error(`chat stream -> ${res.status}`);

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
      const ev = parseEvent(raw);
      if (ev) yield ev;
    }
  }
}
