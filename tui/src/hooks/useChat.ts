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

export type ChatStatus = "connecting" | "idle" | "thinking" | "reconnecting" | "error";

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
  const thinkingRef = useRef(false); // a turn is in flight (drives status after reconnect)

  // Coalesce streamed deltas into ~16 fps repaints. A fast turn can emit 200+
  // deltas in a couple of seconds; calling setPending on each one repaints the
  // whole Ink frame per token and flickers badly (worst on WSL). We accumulate
  // in bufRef and flush on a throttle, with a trailing flush so the last token
  // always lands.
  const FLUSH_MS = 60;
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastFlush = useRef(0);
  const flushNow = () => {
    lastFlush.current = Date.now();
    setPending({ text: bufRef.current.text, steps: [...bufRef.current.steps] });
  };
  const scheduleFlush = () => {
    const since = Date.now() - lastFlush.current;
    if (since >= FLUSH_MS) {
      if (flushTimer.current) {
        clearTimeout(flushTimer.current);
        flushTimer.current = null;
      }
      flushNow();
    } else if (!flushTimer.current) {
      flushTimer.current = setTimeout(() => {
        flushTimer.current = null;
        flushNow();
      }, FLUSH_MS - since);
    }
  };
  const cancelFlush = () => {
    if (flushTimer.current) {
      clearTimeout(flushTimer.current);
      flushTimer.current = null;
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
    const backoff = (ms: number) => Math.min(ms * 2, 8000);

    void (async () => {
      // 1. Create the thread, retrying while the gateway is unreachable rather
      //    than giving up — a cold gateway at launch shouldn't be a dead end.
      let threadId = "";
      let wait = 500;
      while (alive && !threadId) {
        try {
          const t = await client.createThread(`tui-${randomUUID().slice(0, 8)}`, "TUI session");
          threadId = t.id;
          threadRef.current = threadId;
          log.info("chat.thread_created", { threadId });
        } catch (e) {
          if (!alive) return;
          setStatus("reconnecting");
          setError(null);
          log.warn("chat.thread_create_retry", { error: (e as Error).message, wait });
          await sleep(wait);
          wait = backoff(wait);
        }
      }
      if (!alive) return;

      // 2. Stream, reconnecting on drop. The thread lives server-side, so on a
      //    network blip we just reattach to its stream.
      wait = 500;
      while (alive) {
        try {
          log.info("chat.stream_open", { threadId });
          const onOpen = () => {
            wait = 500;
            setError(null);
            setStatus(thinkingRef.current ? "thinking" : "idle");
            log.info("chat.stream_connected", { threadId });
          };
          for await (const ev of streamThread(client.baseUrl(), threadId, controller.signal, onOpen)) {
            if (!alive) break;
            statsRef.current.events += 1;
            switch (ev.type) {
              case "message.delta":
                statsRef.current.deltas += 1;
                statsRef.current.chars += ev.delta.length;
                bufRef.current.text += ev.delta;
                scheduleFlush();
                break;
              case "agent.step":
                bufRef.current.steps.push(ev.step);
                scheduleFlush();
                break;
              case "agent.done": {
                cancelFlush();
                const buf = bufRef.current;
                const s = statsRef.current;
                const reason = describeEmptyTurn(s) ?? undefined;
                log.info("chat.turn_done", {
                  events: s.events,
                  deltas: s.deltas,
                  chars: s.chars,
                  errored: s.errored,
                  reason: reason ?? null,
                });
                setMessages((m) => [
                  ...m,
                  { role: "assistant", text: buf.text, steps: buf.steps, reason },
                ]);
                bufRef.current = { text: "", steps: [] };
                statsRef.current = newTurnStats();
                thinkingRef.current = false;
                setPending(null);
                setStatus("idle");
                break;
              }
              case "error":
                statsRef.current.errored = true;
                statsRef.current.errorMsg = ev.error;
                log.error("chat.stream_error_event", { threadId, error: ev.error });
                setError(ev.error);
                break;
              default:
                break;
            }
          }
          // Clean end (server closed the stream) — reconnect if still mounted.
          if (!alive) break;
          log.warn("chat.stream_closed_reconnecting", { threadId, wait });
          setStatus("reconnecting");
          await sleep(wait);
          wait = backoff(wait);
        } catch (e) {
          if (!alive || controller.signal.aborted) break;
          log.warn("chat.stream_reconnecting", { threadId, error: (e as Error).message, wait });
          setStatus("reconnecting");
          setError(null);
          await sleep(wait);
          wait = backoff(wait);
        }
      }
    })();

    return () => {
      alive = false;
      cancelFlush();
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
      thinkingRef.current = true;
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
