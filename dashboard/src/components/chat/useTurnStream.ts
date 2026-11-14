import { useEffect, useState } from 'react';
import { twinChatStreamUrl } from '../../api/endpoints/twin-chat';
import { getAccessToken } from '../../auth/accessToken';
import { logger } from '../../lib/logger';
import { isSampleMode } from '../../lib/sample-workspace';
import { streamSse } from '../../lib/sse';
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
    if (!threadId || isSampleMode()) return;
    // Read with the fetch-based `streamSse`, not a native EventSource: an
    // EventSource cannot send the bearer token, so behind an authenticating
    // gateway the stream answered 401 and the EventSource's own `error` event
    // was shown as "Something went wrong" while the turn ran on unharmed.
    const kinds = new Set<string>(ACTIVITY_EVENTS);
    const controller = new AbortController();
    const token = getAccessToken();
    streamSse(twinChatStreamUrl(threadId), {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal: controller.signal,
      onMessage: ({ event, data: raw }) => {
        if (!kinds.has(event)) return;
        let data: unknown;
        try {
          data = JSON.parse(raw);
        } catch {
          data = raw;
        }
        const kind = event as (typeof ACTIVITY_EVENTS)[number];
        setEvents((prev) => [...prev.slice(-199), { kind, data, at: new Date().toISOString() }]);
        if (kind === 'agent.done') onDone?.();
      },
    }).catch((err: unknown) => {
      // The live view is a nicety: the reply still arrives by polling, so a
      // stream that could not open is logged, not shown as the turn failing.
      if (!controller.signal.aborted) {
        logger.warn('chat_turn_stream_failed', { threadId, error: String(err) });
      }
    });
    return () => controller.abort();
    // onDone is a callback the caller may recreate each render; the stream follows the thread and the turn.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId, active]);
  return events;
}
