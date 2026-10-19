/**
 * A Server-Sent Events reader built on `fetch` (FORGE-645).
 *
 * The browser's `EventSource` cannot set an `Authorization` header, and a
 * gateway running an authentication provider wants a bearer token on every
 * route, the stream included. `fetch` can send it, so the run stream is read
 * with this instead. The wire format is the same text/event-stream.
 */

export interface SseMessage {
  /** The `event:` name; `message` when the frame names none. */
  event: string;
  data: string;
  /** The `id:` line, when the frame carries one. */
  id?: string;
}

/**
 * An incremental text/event-stream parser. Feed it chunks as they arrive;
 * a frame is emitted once its blank-line terminator has been seen, so a frame
 * split across two chunks is still delivered whole.
 */
export function createSseParser(onMessage: (message: SseMessage) => void) {
  let buffer = '';
  return (chunk: string): void => {
    buffer += chunk.replace(/\r\n/g, '\n');
    let end = buffer.indexOf('\n\n');
    while (end !== -1) {
      const frame = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const message = parseFrame(frame);
      if (message) onMessage(message);
      end = buffer.indexOf('\n\n');
    }
  };
}

function parseFrame(frame: string): SseMessage | null {
  let event = 'message';
  let id: string | undefined;
  const data: string[] = [];
  for (const line of frame.split('\n')) {
    if (line === '' || line.startsWith(':')) continue;
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') event = value;
    else if (field === 'id') id = value;
    else if (field === 'data') data.push(value);
  }
  if (data.length === 0 && id === undefined) return null;
  return { event, data: data.join('\n'), id };
}

export interface StreamSseOptions {
  headers?: Record<string, string>;
  signal?: AbortSignal;
  onMessage: (message: SseMessage) => void;
  fetchImpl?: typeof fetch;
}

/**
 * Open a stream and deliver its messages until the server ends it or `signal`
 * aborts. Resolves when the stream ends cleanly; rejects on a non-2xx answer
 * or a network error, so the caller can decide whether to reconnect.
 */
export async function streamSse(url: string, options: StreamSseOptions): Promise<void> {
  const doFetch = options.fetchImpl ?? fetch;
  const response = await doFetch(url, {
    headers: { Accept: 'text/event-stream', ...options.headers },
    signal: options.signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`stream ${url} answered ${response.status}`);
  }
  const parse = createSseParser(options.onMessage);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    parse(decoder.decode(value, { stream: true }));
  }
}
