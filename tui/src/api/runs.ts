/**
 * Run status SSE client for /v1/runs/{id}/events. The gateway emits a single
 * event type `run.status` with payload {id, status, updated_at,
 * approval_reason, error} (api_gateway/runs/streaming.py). `streamRunStatus`
 * yields those snapshots live until the run reaches a terminal status.
 */

export interface RunStatusEvent {
  id: string;
  status: string;
  updated_at?: number;
  approval_reason?: string | null;
  error?: string | null;
}

export async function* streamRunStatus(
  base: string,
  runId: string,
  signal: AbortSignal,
): AsyncGenerator<RunStatusEvent> {
  const res = await fetch(`${base}/v1/runs/${runId}/events`, {
    signal,
    headers: { Accept: "text/event-stream" },
  });
  if (!res.ok || !res.body) throw new Error(`run stream -> ${res.status}`);

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
      let dataStr = "";
      for (const line of raw.split("\n")) {
        if (line.startsWith("data:")) dataStr += line.slice(5).trim();
      }
      if (!dataStr) continue;
      try {
        yield JSON.parse(dataStr) as RunStatusEvent;
      } catch {
        // ignore malformed frame
      }
    }
  }
}

const TERMINAL = new Set(["completed", "failed", "rejected", "canceled"]);
export function isTerminal(status: string | undefined): boolean {
  return status !== undefined && TERMINAL.has(status);
}
