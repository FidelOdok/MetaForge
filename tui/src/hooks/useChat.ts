import { useCallback, useEffect, useRef, useState } from "react";
import { GatewayError, type GatewayClient } from "../api/client.js";
import { streamThread, type AgentStep, type ContextStats } from "../api/chat.js";
import { describeEmptyTurn, newTurnStats, type TurnStats } from "../chat-diagnostics.js";
import { assistantScope, scopeKey, type ChatScope } from "../lib/project.js";
import { log } from "../log.js";

export interface ChatMessage {
  /** `system` = a local notice in the transcript (scope change, degraded scope). */
  role: "user" | "assistant" | "system";
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
  pending: {
    text: string;
    steps: AgentStep[];
    thinking?: string;
    startedAction?: string;
  } | null;
  /** Most recent per-turn context-window snapshot, or null before the first turn. */
  contextStats: ContextStats | null;
  /**
   * Scope of the thread that actually exists server-side — null until one does.
   * The status line renders this (not the requested scope) so it can never claim
   * a project the agent isn't working in.
   */
  threadScope: ChatScope | null;
  send: (content: string) => void;
}

/**
 * Owns a chat thread: creates it, opens the SSE stream once, and folds
 * message.delta / agent.step / agent.done into message state. Accumulation
 * uses a ref so the long-lived stream loop never reads stale React state.
 *
 * `scope` decides which thread gets created. Changing it (e.g. `/project`)
 * starts a **new** thread in the new scope — a thread's scope is immutable
 * server-side, so switching projects necessarily means leaving the old
 * conversation's context behind. Pass `null` to hold off until the caller has
 * resolved the scope (e.g. while looking up a `--project` name), so a throwaway
 * assistant thread isn't created first.
 */
export function useChat(
  client: GatewayClient,
  model?: string,
  provider?: string,
  scope: ChatScope | null = assistantScope(),
): UseChat {
  const [status, setStatus] = useState<ChatStatus>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState<{
    text: string;
    steps: AgentStep[];
    thinking?: string;
    startedAction?: string;
  } | null>(
    null,
  );
  const [contextStats, setContextStats] = useState<ContextStats | null>(null);
  const [threadScope, setThreadScope] = useState<ChatScope | null>(null);

  const threadRef = useRef<string | null>(null);
  // Scope of the previous thread, so a *change* can be announced in the
  // transcript while the first thread of a session stays quiet.
  const priorScope = useRef<ChatScope | null>(null);
  const bufRef = useRef<{
    text: string;
    steps: AgentStep[];
    thinking: string;
    startedAction: string;
  }>({ text: "", steps: [], thinking: "", startedAction: "" });
  const statsRef = useRef<TurnStats>(newTurnStats());
  const thinkingRef = useRef(false); // a turn is in flight (drives status after reconnect)
  const turnSeq = useRef(0); // bumped per send; guards the fallback finalizer against a stale turn
  const fallbackTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // MET-590: idle watchdog. The gateway now streams agent.step events live,
  // so "healthy but slow" and "hung" are distinguishable: as long as progress
  // events keep arriving we keep the turn's POST alive (up to the client's
  // 45-min hard ceiling); only sustained SILENCE aborts it.
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const turnAbort = useRef<AbortController | null>(null);

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
    setPending({
      text: bufRef.current.text,
      steps: [...bufRef.current.steps],
      thinking: bufRef.current.thinking,
      startedAction: bufRef.current.startedAction,
    });
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

  // End the in-flight turn exactly once: commit the buffered answer as a message
  // and go idle. Called by BOTH the SSE `agent.done` event and the send() POST
  // resolving — whichever happens first wins (guarded on `thinkingRef`). This is
  // what stops a lost `agent.done` (dropped on an SSE reconnect) from leaving the
  // chat stuck on "thinking" forever: the POST resolves only when the turn is
  // done server-side, so it's an authoritative fallback terminal signal.
  const IDLE_ABORT_MS = 300000; // 5 min with no progress events => hung turn
  const clearIdle = () => {
    if (idleTimer.current) {
      clearTimeout(idleTimer.current);
      idleTimer.current = null;
    }
  };
  const pokeIdle = () => {
    if (!thinkingRef.current || !turnAbort.current) return;
    clearIdle();
    const ac = turnAbort.current;
    idleTimer.current = setTimeout(() => {
      log.warn("chat.turn_idle_abort", { idleMs: IDLE_ABORT_MS });
      ac.abort(new Error(`no progress events for ${IDLE_ABORT_MS / 60000} minutes`));
    }, IDLE_ABORT_MS);
  };

  const finalizeTurn = (fallback?: string) => {
    if (!thinkingRef.current) return; // already finalized by the other path
    thinkingRef.current = false;
    clearIdle();
    turnAbort.current = null;
    if (fallbackTimer.current) {
      clearTimeout(fallbackTimer.current);
      fallbackTimer.current = null;
    }
    cancelFlush();
    const buf = bufRef.current;
    const s = statsRef.current;
    const emptyReason = describeEmptyTurn(s) ?? undefined;
    const reason = buf.text ? undefined : (emptyReason ?? fallback);
    log.info("chat.turn_done", {
      events: s.events,
      deltas: s.deltas,
      chars: s.chars,
      errored: s.errored,
      reason: reason ?? null,
      fallback: fallback ?? null,
    });
    setMessages((m) => [...m, { role: "assistant", text: buf.text, steps: buf.steps, reason }]);
    bufRef.current = { text: "", steps: [], thinking: "", startedAction: "" };
    statsRef.current = newTurnStats();
    setPending(null);
    setStatus("idle");
  };

  const key = scopeKey(scope);
  useEffect(() => {
    if (scope === null) return; // caller is still resolving the scope
    const controller = new AbortController();
    let alive = true;
    const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
    const backoff = (ms: number) => Math.min(ms * 2, 8000);
    const note = (text: string) => setMessages((m) => [...m, { role: "system", text }]);

    // Nothing about the previous thread survives a scope change except the
    // transcript already committed to the terminal's scrollback (clearing
    // `messages` would fight Ink's <Static>, which only ever appends). Drop the
    // thread handle first so an in-flight send can't be routed to the old thread.
    threadRef.current = null;
    setThreadScope(null);
    setContextStats(null);
    setPending(null);
    thinkingRef.current = false;
    setStatus("connecting");

    void (async () => {
      // 1. Create the thread, retrying while the gateway is unreachable rather
      //    than giving up — a cold gateway at launch shouldn't be a dead end.
      //    A 4xx on a project scope is different: the gateway has answered and
      //    won't change its mind (e.g. no channel seeded for `scope_kind=project`),
      //    so retrying is pointless. Say so and continue unscoped rather than
      //    wedging the chat or — worse — leaving the UI showing a project scope
      //    the thread doesn't have.
      let threadId = "";
      let wait = 500;
      let effective = scope;
      while (alive && !threadId) {
        try {
          const t = await client.createThread(effective, "TUI session");
          threadId = t.id;
          threadRef.current = threadId;
          log.info("chat.thread_created", { threadId, scope: scopeKey(effective) });
        } catch (e) {
          if (!alive) return;
          if (
            effective.kind === "project" &&
            e instanceof GatewayError &&
            e.status !== undefined &&
            e.status < 500
          ) {
            log.error("chat.project_scope_rejected", { error: e.message, status: e.status });
            note(`project scope unavailable (${e.message}) — continuing with no project`);
            effective = assistantScope();
            continue;
          }
          setStatus("reconnecting");
          setError(null);
          log.warn("chat.thread_create_retry", { error: (e as Error).message, wait });
          await sleep(wait);
          wait = backoff(wait);
        }
      }
      if (!alive) return;
      setThreadScope(effective);
      // Announce a switch (not the first thread of the session): the new thread
      // starts empty server-side, and pretending otherwise would be a lie the
      // user only discovers when the agent forgets the conversation.
      if (priorScope.current !== null && scopeKey(priorScope.current) !== scopeKey(effective)) {
        note(
          effective.kind === "project"
            ? `— project → ${effective.name} · new thread, earlier context not carried over —`
            : "— left the project · new thread, earlier context not carried over —",
        );
      }
      priorScope.current = effective;

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
            pokeIdle(); // MET-590: any event = progress; keep the turn alive
            switch (ev.type) {
              case "message.delta":
                if (!thinkingRef.current) break; // stray event after finalize
                statsRef.current.deltas += 1;
                statsRef.current.chars += ev.delta.length;
                bufRef.current.text += ev.delta;
                scheduleFlush();
                break;
              case "agent.step":
                if (!thinkingRef.current) break;
                bufRef.current.steps.push(ev.step);
                // The step carries the full thought this draft belonged to —
                // clear the live draft + started-action marker.
                bufRef.current.thinking = "";
                bufRef.current.startedAction = "";
                scheduleFlush();
                break;
              case "agent.action_started":
                if (!thinkingRef.current) break;
                bufRef.current.startedAction = ev.tool;
                scheduleFlush();
                break;
              case "agent.thinking":
                if (!thinkingRef.current) break;
                bufRef.current.thinking += ev.delta;
                scheduleFlush();
                break;
              case "context.stats":
                setContextStats(ev.stats);
                break;
              case "scope.changed": {
                // The agent's chat.set_project_scope tool rescoped THIS thread
                // in place (MET-580) — no new thread, so this is the only
                // signal the client gets. Update state and notice it exactly
                // like a human-initiated switch, since the model's own prose
                // reply is not a substitute (it might not mention it, or might
                // vary in wording) — the transcript record must not depend on
                // the model choosing to say so.
                const next: ChatScope =
                  ev.scope.scope_kind === "project"
                    ? {
                        kind: "project",
                        id: ev.scope.scope_entity_id,
                        name: ev.scope.project_name ?? ev.scope.scope_entity_id,
                      }
                    : assistantScope();
                setThreadScope(next);
                note(
                  next.kind === "project"
                    ? `— project → ${next.name} (set by the agent) —`
                    : "— left the project (set by the agent) —",
                );
                priorScope.current = next;
                break;
              }
              case "agent.done":
                finalizeTurn();
                break;
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
      if (fallbackTimer.current) clearTimeout(fallbackTimer.current);
      controller.abort();
    };
    // `key` (not `scope`) is the dependency: an assistant scope carries a random
    // entity id, so comparing the object itself would recreate the thread every
    // render.
  }, [client, key]);

  const send = useCallback(
    (content: string) => {
      const threadId = threadRef.current;
      if (!threadId || !content.trim()) return;
      const myTurn = (turnSeq.current += 1);
      if (fallbackTimer.current) clearTimeout(fallbackTimer.current);
      setMessages((m) => [...m, { role: "user", text: content }]);
      bufRef.current = { text: "", steps: [], thinking: "", startedAction: "" };
      statsRef.current = newTurnStats();
      thinkingRef.current = true;
      turnAbort.current = new AbortController();
      pokeIdle(); // arm the idle watchdog for this turn
      setPending({ text: "", steps: [], thinking: "", startedAction: "" });
      setStatus("thinking");
      log.info("chat.send", { threadId, chars: content.length, model, provider });

      // Fallback terminal signal so a lost `agent.done` can't wedge the chat.
      // The message POST resolves when the turn is done (real gateway; the turn
      // runs inside the POST) OR immediately (async backends), so on resolve we
      // wait a short grace for the SSE `agent.done` and only finalize ourselves
      // if it never arrives — and only if this is still the active turn.
      const GRACE_MS = 2500;
      const armFallback = (fallback: string) => {
        if (fallbackTimer.current) clearTimeout(fallbackTimer.current);
        fallbackTimer.current = setTimeout(() => {
          if (turnSeq.current === myTurn) finalizeTurn(fallback);
        }, GRACE_MS);
      };
      void client
        .sendMessage(threadId, content, { model, provider, signal: turnAbort.current.signal })
        .then(
        () => armFallback("stream ended without a completion event"),
        (e: Error) => {
          log.error("chat.send_failed", { threadId, error: e.message });
          if (turnSeq.current === myTurn && thinkingRef.current) {
            setError(`send: ${e.message}`);
            armFallback(`request failed: ${e.message}`);
          }
        },
      );
    },
    [client, model, provider],
  );

  return { status, error, messages, pending, contextStats, threadScope, send };
}
