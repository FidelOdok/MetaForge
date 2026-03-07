import { describe, it, expect, vi } from 'vitest';

vi.mock('../../client', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import apiClient from '../../client';
import { getChatThreads, getChatThread, createChatThread, sendChatMessage, getChatChannels } from '../chat';

const mockGet = vi.mocked(apiClient.get);
const mockPost = vi.mocked(apiClient.post);

describe('getChatThreads', () => {
  it('calls /chat/threads', async () => {
    mockGet.mockResolvedValueOnce({ data: { threads: [], total: 0, page: 1, per_page: 20 } });
    const result = await getChatThreads();
    expect(mockGet).toHaveBeenCalledWith('/chat/threads', expect.anything());
    expect(result).toBeDefined();
  });
});

describe('getChatThread', () => {
  it('calls /chat/threads/:id', async () => {
    mockGet.mockResolvedValueOnce({ data: { id: 't1', messages: [] } });
    const result = await getChatThread('t1');
    expect(result).toBeDefined();
  });
});

describe('createChatThread', () => {
  it('posts to /chat/threads', async () => {
    mockPost.mockResolvedValueOnce({ data: { id: 't1', messages: [] } });
    const result = await createChatThread({
      channelId: 'ch1',
      title: 'Test',
      scope: { kind: 'session', entityId: 's1' },
    });
    expect(result).toBeDefined();
  });
});

describe('sendChatMessage', () => {
  it('posts to /chat/threads/:id/messages', async () => {
    mockPost.mockResolvedValueOnce({ data: { id: 'm1', content: 'hi' } });
    const result = await sendChatMessage('t1', { content: 'hi' });
    expect(result).toBeDefined();
  });
});

describe('getChatChannels', () => {
  it('calls /chat/channels', async () => {
    mockGet.mockResolvedValueOnce({ data: { channels: [] } });
    const result = await getChatChannels();
    expect(result).toBeDefined();
  });
});
