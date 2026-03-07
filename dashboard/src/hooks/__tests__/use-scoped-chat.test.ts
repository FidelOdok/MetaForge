import { describe, it, expect, vi } from 'vitest';
import { renderHook } from '../../test/test-utils';

vi.mock('../use-chat', () => ({
  useChatThreads: vi.fn().mockReturnValue({ data: { data: [] }, isLoading: false }),
  useChatThread: vi.fn().mockReturnValue({ data: null, isLoading: false }),
  useCreateChatThread: vi.fn().mockReturnValue({ mutate: vi.fn(), isPending: false }),
  useSendChatMessage: vi.fn().mockReturnValue({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('../../store/chat-store', () => ({
  useChatStore: vi.fn((selector: (state: { typingThreadIds: Set<string> }) => unknown) =>
    selector({ typingThreadIds: new Set() }),
  ),
}));

import { useScopedChat } from '../use-scoped-chat';

describe('useScopedChat', () => {
  it('returns null thread when none exists', () => {
    const { result } = renderHook(() =>
      useScopedChat({ scopeKind: 'session', entityId: 'test-id' }),
    );
    expect(result.current.thread).toBeNull();
    expect(result.current.messages).toEqual([]);
    expect(result.current.isTyping).toBe(false);
  });

  it('provides sendMessage and createThread callbacks', () => {
    const { result } = renderHook(() =>
      useScopedChat({ scopeKind: 'approval', entityId: 'a1' }),
    );
    expect(typeof result.current.sendMessage).toBe('function');
    expect(typeof result.current.createThread).toBe('function');
  });
});
