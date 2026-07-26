import { useCallback, useEffect, useRef, useState } from "react";
import { randomUUID } from "node:crypto";
import type { GatewayClient } from "../api/client.js";
import { streamThread, type AgentStep } from "../api/chat.js";

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  steps?: AgentStep[];
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

  useEffect(() => {
    const controller = new AbortController();
    let alive = true;

    void (async () => {
      let threadId: string;
      try {
        const thread = await client.createThread(`tui-${randomUUID().slice(0, 8)}`, "TUI session");
        threadId = thread.id;
        threadRef.current = threadId;
      } catch (e) {
        if (alive) {
          setStatus("error");
          setError(`create thread: ${(e as Error).message}`);
        }
        return;
      }
      if (alive) setStatus("idle");

      try {
        for await (const ev of streamThread(client.baseUrl(), threadId, controller.signal)) {
          if (!alive) break;
          switch (ev.type) {
            case "message.delta":
              bufRef.current.text += ev.delta;
              setPending({ text: bufRef.current.text, steps: [...bufRef.current.steps] });
              break;
            case "agent.step":
              bufRef.current.steps.push(ev.step);
              setPending({ text: bufRef.current.text, steps: [...bufRef.current.steps] });
              break;
            case "agent.done": {
              const buf = bufRef.current;
              // Always commit a turn's result so an exhausted/empty turn shows a
              // "(no reply)" line rather than a confusing blank assistant.
              setMessages((m) => [
                ...m,
                { role: "assistant", text: buf.text, steps: buf.steps },
              ]);
              bufRef.current = { text: "", steps: [] };
              setPending(null);
              setStatus("idle");
              break;
            }
            case "error":
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
      setPending({ text: "", steps: [] });
      setStatus("thinking");
      void client.sendMessage(threadId, content, { model, provider }).catch((e: Error) => {
        setStatus("error");
        setError(`send: ${e.message}`);
        setPending(null);
      });
    },
    [client, model, provider],
  );

  return { status, error, messages, pending, send };
}
