import { useEffect, useState } from 'react';
import { twinChatStreamUrl } from '../../api/endpoints/twin-chat';
import { isSampleMode } from '../../lib/sample-workspace';
import type { StreamActivity } from './activity';

/** Stream events that describe what the agent is doing (not the reply text). */
export const ACTIVITY_EVENTS = [
  'agent.thinking',
  'agent.action_started',
  'agent.step',
  'tool.approval_requested',
  'scope.changed',
  'error',
  'agent.done',
] as const;

/**
 * Live agent events for a thread while a turn is running. Opens the thread's
 * SSE stream only while `active`, and starts from an empty list for each
 * turn, so a chat can show what the agent is doing instead of sitting blank.
 * `onDone` fires when the agent reports the turn over, so the caller can
 * fetch the reply at once instead of waiting for its next poll.
 */
export function useTurnStream(threadId: string | null, active: boolean, onDone?: () => void): StreamActivity[] {
  const [events, setEvents] = useState<StreamActivity[]>([]);
  useEffect(() => {
    if (!active) return;
    setEvents([]);
    if (!threadId || isSampleMode() || typeof EventSource === 'undefined') return;
    const source = new EventSource(twinChatStreamUrl(threadId));
    for (const kind of ACTIVITY_EVENTS) {
      source.addEventListener(kind, (evt) => {
        const raw = (evt as MessageEvent).data;
        let data: unknown;
        try {
          data = JSON.parse(raw);
        } catch {
          data = raw;
        }
        setEvents((prev) => [...prev.slice(-199), { kind, data, at: new Date().toISOString() }]);
        if (kind === 'agent.done') onDone?.();
      });
    }
    return () => source.close();
    // onDone is a callback the caller may recreate each render; the stream follows the thread and the turn.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId, active]);
  return events;
}
