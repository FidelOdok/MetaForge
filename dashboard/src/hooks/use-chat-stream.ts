import { useEffect } from 'react';
import { useChatStore } from '@/store/chat-store';

/**
 * Subscribe to a chat thread's Server-Sent Events stream and feed the harness's
 * `message.delta` chunks into the chat store, so the agent's answer renders
 * token-by-token (MET-548). Also drives the typing indicator from the
 * `agent.typing` / `agent.done` events.
 *
 * The EventSource is opened per thread and torn down on unmount / thread
 * change, clearing any partial streaming content. Malformed events are ignored
 * rather than crashing the stream.
 */
export function useChatStream(threadId: string | null): void {
  const appendStreamChunk = useChatStore((s) => s.appendStreamChunk);
  const setAgentTyping = useChatStore((s) => s.setAgentTyping);
  const clearStreamContent = useChatStore((s) => s.clearStreamContent);

  useEffect(() => {
    if (!threadId) return;

    // baseURL is '/api/v1' (Vite-proxied to the gateway); EventSource needs a
    // raw URL (axios can't do SSE).
    const source = new EventSource(`/api/v1/chat/threads/${threadId}/stream`);

    const onDelta = (ev: MessageEvent) => {
      try {
        const payload = JSON.parse(ev.data) as { data?: { delta?: string } };
        const delta = payload.data?.delta ?? '';
        if (delta) {
          appendStreamChunk(threadId, delta);
          setAgentTyping(threadId, true);
        }
      } catch {
        // Ignore malformed SSE payloads — keep the stream alive.
      }
    };
    const onTyping = () => setAgentTyping(threadId, true);
    const onDone = () => setAgentTyping(threadId, false);

    source.addEventListener('message.delta', onDelta as EventListener);
    source.addEventListener('agent.typing', onTyping as EventListener);
    source.addEventListener('agent.done', onDone as EventListener);

    return () => {
      source.removeEventListener('message.delta', onDelta as EventListener);
      source.removeEventListener('agent.typing', onTyping as EventListener);
      source.removeEventListener('agent.done', onDone as EventListener);
      source.close();
      setAgentTyping(threadId, false);
      clearStreamContent(threadId);
    };
  }, [threadId, appendStreamChunk, setAgentTyping, clearStreamContent]);
}
