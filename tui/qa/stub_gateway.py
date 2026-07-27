"""Minimal stub MetaForge gateway for hermetic TUI QA.

Implements only the endpoints the `forge` TUI touches at startup and during a
chat turn, so the real binary can be exercised without a live gateway. The chat
stream emits scripted SSE:

- normal: enveloped `message.delta` frames spelling a reply, then `agent.done`.
- malformed (message contains the ``__malformed__`` sentinel): enveloped
  `message.delta` frames whose ``data`` has *no* ``delta`` field, reproducing the
  exact "deltas arrived but 0 characters" payload/parse mismatch behind the
  historical "(no reply)" bug — so QA can assert the cause is surfaced.

Run standalone (`python3 stub_gateway.py`) or via `start_stub()` from the QA
harness.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MALFORMED = "__malformed__"
REPLY_TOKENS = ["Hello", "! ", "This ", "is ", "the ", "QA ", "stub ", "reply."]

# Per-thread queues bridge POST /messages -> the open GET /stream (like the real
# gateway's pub/sub). A posted message's content lands here; the stream drains it.
_pending: dict[str, queue.Queue[str]] = {}
_lock = threading.Lock()


def _q(tid: str) -> queue.Queue[str]:
    with _lock:
        return _pending.setdefault(tid, queue.Queue())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:  # keep QA output clean
        pass

    def _json(self, code: int, obj: object) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        p = self.path
        if p == "/health":
            return self._json(200, {"status": "healthy", "version": "qa-stub"})
        if p == "/v1/projects":
            return self._json(200, {"projects": [{"id": "p1", "name": "QA Project"}]})
        if p == "/v1/runs":
            return self._json(200, {"runs": []})
        if p == "/v1/twin/nodes":
            return self._json(200, {"nodes": []})
        if p.endswith("/stream"):
            return self._stream(p.split("/threads/")[1].split("/")[0])
        if "/v1/chat/threads/" in p:
            return self._json(200, {"messages": []})
        return self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = {}
        p = self.path
        if p == "/v1/chat/threads":
            return self._json(201, {"id": "qa-thread"})
        if p.endswith("/messages"):
            tid = p.split("/threads/")[1].split("/")[0]
            _q(tid).put(str(data.get("content", "")))
            return self._json(201, {"id": "m1"})
        return self._json(404, {"error": "not found"})

    def _stream(self, tid: str) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()
        # Stay open for the thread's lifetime; script one reply per posted message.
        while True:
            try:
                content = _q(tid).get(timeout=120)
            except queue.Empty:
                return
            self._emit("agent.typing", {"agent_id": "qa"}, tid)
            if MALFORMED in content:
                # Enveloped delta events with NO `delta` field -> parse to "".
                for _ in range(2):
                    if not self._emit("message.delta", {"agent_id": "agent"}, tid):
                        return
                    time.sleep(0.05)
            else:
                for tok in REPLY_TOKENS:
                    if not self._emit("message.delta", {"delta": tok, "agent_id": "agent"}, tid):
                        return
                    time.sleep(0.05)
            self._emit("agent.done", {"agent_id": "qa"}, tid)

    def _emit(self, event: str, data: dict, tid: str) -> bool:
        env = {"data": data, "thread_id": tid, "timestamp": "qa"}
        try:
            self.wfile.write(f"event: {event}\ndata: {json.dumps(env)}\n\n".encode())
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False


def start_stub(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


if __name__ == "__main__":
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
    server, url = start_stub(port)
    print(f"stub gateway on {url}  (Ctrl+C to stop)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
