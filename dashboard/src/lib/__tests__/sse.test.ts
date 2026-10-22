import { describe, expect, it } from 'vitest';

import { createSseParser, streamSse, type SseMessage } from '../sse';

function collect() {
  const messages: SseMessage[] = [];
  return { messages, parse: createSseParser((m) => messages.push(m)) };
}

describe('createSseParser', () => {
  it('parses a frame with an id, an event name and data', () => {
    const { messages, parse } = collect();
    parse('id: 3\nevent: tool.progress\ndata: {"tool":"t"}\n\n');
    expect(messages).toEqual([{ event: 'tool.progress', data: '{"tool":"t"}', id: '3' }]);
  });

  it('delivers a frame split across chunks once, whole', () => {
    const { messages, parse } = collect();
    parse('event: run.status\nda');
    expect(messages).toHaveLength(0);
    parse('ta: {"status":"running"}\n');
    expect(messages).toHaveLength(0);
    parse('\n');
    expect(messages).toEqual([
      { event: 'run.status', data: '{"status":"running"}', id: undefined },
    ]);
  });

  it('handles several frames in one chunk, CRLF, and comments', () => {
    const { messages, parse } = collect();
    parse(': keep-alive\r\n\r\nevent: a\r\ndata: 1\r\n\r\nevent: b\r\ndata: 2\r\n\r\n');
    expect(messages.map((m) => [m.event, m.data])).toEqual([
      ['a', '1'],
      ['b', '2'],
    ]);
  });

  it('names a frame without an event "message" and joins multi-line data', () => {
    const { messages, parse } = collect();
    parse('data: one\ndata: two\n\n');
    expect(messages).toEqual([{ event: 'message', data: 'one\ntwo', id: undefined }]);
  });
});

function streamOf(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
  return new Response(body, { status: 200 });
}

describe('streamSse', () => {
  it('sends the given headers and delivers messages until the stream ends', async () => {
    const seen: SseMessage[] = [];
    let sent: RequestInit | undefined;
    const fetchImpl = (async (_url: unknown, init?: RequestInit) => {
      sent = init;
      return streamOf(['event: tool.progress\ndata: {}\n\n', 'event: run.status\ndata: {}\n\n']);
    }) as typeof fetch;
    await streamSse('/x', {
      headers: { Authorization: 'Bearer t', 'Last-Event-ID': '0' },
      onMessage: (m) => seen.push(m),
      fetchImpl,
    });
    expect(seen.map((m) => m.event)).toEqual(['tool.progress', 'run.status']);
    const headers = sent?.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer t');
    expect(headers['Last-Event-ID']).toBe('0');
  });

  it('rejects on a non-2xx answer so the caller can reconnect', async () => {
    const fetchImpl = (async () => new Response('no', { status: 401 })) as typeof fetch;
    await expect(streamSse('/x', { onMessage: () => {}, fetchImpl })).rejects.toThrow('401');
  });
});
