import { useQuery } from '@tanstack/react-query';
import { getTwinChatThread, listTwinChatThreads } from '../api/endpoints/twin-chat';
import { getSessions } from '../api/endpoints/sessions';

export const twinChatKeys = {
  all: ['twin-chat'] as const,
  threads: (projectId?: string | null) => ['twin-chat', 'threads', projectId ?? null] as const,
  thread: (projectId?: string | null, threadId?: string | null) =>
    ['twin-chat', 'thread', projectId ?? null, threadId ?? null] as const,
  captured: (projectId?: string | null) => ['twin-chat', 'captured', projectId ?? null] as const,
};

/** Project-scoped conversation threads for the twin agent drawer. */
export function useTwinChatThreads(projectId: string | null | undefined) {
  return useQuery({
    queryKey: twinChatKeys.threads(projectId),
    queryFn: () => listTwinChatThreads(projectId as string),
    enabled: !!projectId,
  });
}

/** One thread with messages; polls faster while a turn is in flight. */
export function useTwinChatThread(
  projectId: string | null | undefined,
  threadId: string | null | undefined,
  busy: boolean,
) {
  return useQuery({
    queryKey: twinChatKeys.thread(projectId, threadId),
    queryFn: () => getTwinChatThread(threadId as string),
    enabled: !!projectId && !!threadId,
    refetchInterval: busy ? 2_000 : 10_000,
  });
}

/** Read-only sessions captured from MCP clients, shown in the thread picker. */
export function useCapturedSessions(projectId: string | null | undefined, enabled: boolean) {
  return useQuery({
    queryKey: twinChatKeys.captured(projectId),
    queryFn: () => getSessions(projectId ?? undefined),
    enabled: !!projectId && enabled,
  });
}
