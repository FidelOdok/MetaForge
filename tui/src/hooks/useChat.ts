import { useCallback, useEffect, useRef, useState } from "react";
import { randomUUID } from "node:crypto";
import type { GatewayClient } from "../api/client.js";
import { streamThread, type AgentStep } from "../api/chat.js";
import { describeEmptyTurn, newTurnStats, type TurnStats } from "../chat-diagnostics.js";
import { log } from "../log.js";

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  steps?: AgentStep[];
  /** Cause of an empty turn (set only when `text` is empty). */
  reason?: string;
}

export type ChatStatus = "connecting" | "idle" | "thinking" | "error";

export interface UseChat {
  status: ChatStatus;
  error: string | null;
  messages: ChatMessage[];
  /** In-flight assistant turn (text + tool steps), or null when idle. */
  pending: { text: string; steps: AgentStep[] } | null;
  send: (content: string) => void;
}

/**
 * Owns a chat thread: creates it, opens the SSE stream once, and folds
 * message.delta / agent.step / agent.done into message state. Accumulation
 * uses a ref so the long-lived stream loop never reads stale React state.
 */
export function useChat(client: GatewayClient, model?: string, provider?: string): UseChat {
  const [status, setStatus] = useState<ChatStatus>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState<{ text: string; steps: AgentStep[] } | null>(null);

  const threadRef = useRef<string | null>(null);
  const bufRef = useRef<{ text: string; steps: AgentStep[] }>({ text: "", steps: [] });
  const statsRef = useRef<TurnStats>(newTurnStats());

  useEffect(() => {
    const controller = new AbortController();
    let alive = true;

    void (async () => {
      let threadId: string;
      try {
        const thread = await client.createThread(`tui-${randomUUID().slice(0, 8)}`, "TUI session");
        threadId = thread.id;
        threadRef.current = threadId;
        log.info("chat.thread_created", { threadId });
      } catch (e) {
        if (alive) {
          setStatus("error");
          setError(`create thread: ${(e as Error).message}`);
        }
        log.error("chat.thread_create_failed", { error: (e as Error).message });
        return;
      }
      if (alive) setStatus("idle");

      try {
        log.info("chat.stream_open", { threadId });
        for await (const ev of streamThread(client.baseUrl(), threadId, controller.signal)) {
          if (!alive) break;
          statsRef.current.events += 1;
          switch (ev.type) {
            case "message.delta":
              statsRef.current.deltas += 1;
              statsRef.current.chars += ev.delta.length;
              bufRef.current.text += ev.delta;
              setPending({ text: bufRef.current.text, steps: [...bufRef.current.steps] });
              break;
            case "agent.step":
              bufRef.current.steps.push(ev.step);
              setPending({ text: bufRef.current.text, steps: [...bufRef.current.steps] });
              break;
            case "agent.done": {
              const buf = bufRef.current;
              const s = statsRef.current;
              const reason = describeEmptyTurn(s) ?? undefined;
              // One structured line per turn — the record that would have made
              // the "(no reply)" bug a `tail` instead of a pty repro.
              log.info("chat.turn_done", {
                events: s.events,
                deltas: s.deltas,
                chars: s.chars,
                errored: s.errored,
                reason: reason ?? null,
              });
              // Always commit a turn's result; carry the cause so an empty turn
              // shows *why* it was empty rather than an opaque "(no reply)".
              setMessages((m) => [
                ...m,
                { role: "assistant", text: buf.text, steps: buf.steps, reason },
              ]);
              bufRef.current = { text: "", steps: [] };
              statsRef.current = newTurnStats();
              setPending(null);
              setStatus("idle");
              break;
            }
            case "error":
              statsRef.current.errored = true;
              statsRef.current.errorMsg = ev.error;
              log.error("chat.stream_error_event", { threadId, error: ev.error });
              setError(ev.error);
              setStatus("error");
              break;
            default:
              break;
          }
        }
      } catch (e) {
        if (alive && !controller.signal.aborted) {
          setStatus("error");
          setError(`stream: ${(e as Error).message}`);
        }
        log.error("chat.stream_failed", { threadId, error: (e as Error).message });
      }
    })();

    return () => {
      alive = false;
      controller.abort();
    };
  }, [client]);

  const send = useCallback(
    (content: string) => {
      const threadId = threadRef.current;
      if (!threadId || !content.trim()) return;
      setMessages((m) => [...m, { role: "user", text: content }]);
      bufRef.current = { text: "", steps: [] };
      statsRef.current = newTurnStats();
      setPending({ text: "", steps: [] });
      setStatus("thinking");
      log.info("chat.send", { threadId, chars: content.length, model, provider });
      void client.sendMessage(threadId, content, { model, provider }).catch((e: Error) => {
        setStatus("error");
        setError(`send: ${e.message}`);
        log.error("chat.send_failed", { threadId, error: e.message });
        setPending(null);
      });
    },
    [client, model, provider],
  );

  return { status, error, messages, pending, send };
}
