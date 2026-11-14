import { waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderHook } from '../../../test/test-utils';
import { setAccessToken } from '../../../auth/accessToken';
import { useTurnStream } from '../useTurnStream';

/**
 * The turn stream must carry the bearer token: behind an authenticating
 * gateway a native EventSource (which cannot send one) got a 401, and its
 * transport `error` event showed "Something went wrong" while the turn ran on.
 */

const streamSse = vi.hoisted(() => vi.fn());
vi.mock('../../../lib/sse', () => ({ streamSse }));

describe('useTurnStream', () => {
  beforeEach(() => {
    streamSse.mockReset();
    setAccessToken('tok-123');
  });

  afterEach(() => {
    setAccessToken(null);
  });

  it('sends the bearer token and delivers activity events', async () => {
    const onDone = vi.fn();
    type Opts = { onMessage: (m: { event: string; data: string }) => void };
    streamSse.mockImplementation(async (_url: string, opts: Opts) => {
      opts.onMessage({ event: 'agent.step', data: '{"step":1}' });
      opts.onMessage({ event: 'heartbeat', data: '' });
      opts.onMessage({ event: 'agent.done', data: '{}' });
    });
    const { result } = renderHook(() => useTurnStream('thread-1', true, onDone));
    await waitFor(() => expect(result.current.map((e) => e.kind)).toEqual(['agent.step', 'agent.done']));
    const call = streamSse.mock.calls[0] ?? [];
    expect(String(call[0])).toContain('/chat/threads/thread-1/stream');
    expect(call[1]?.headers).toEqual({ Authorization: 'Bearer tok-123' });
    expect(result.current[0]?.data).toEqual({ step: 1 });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('does not report a stream that failed to open as a turn error', async () => {
    streamSse.mockRejectedValue(new Error('stream answered 401'));
    const { result } = renderHook(() => useTurnStream('thread-1', true));
    await waitFor(() => expect(streamSse).toHaveBeenCalled());
    expect(result.current).toEqual([]);
  });

  it('opens nothing while no turn is running', () => {
    renderHook(() => useTurnStream('thread-1', false));
    expect(streamSse).not.toHaveBeenCalled();
  });
});
