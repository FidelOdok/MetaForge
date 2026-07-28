import { useRef, useState } from "react";
import { Box, Static, Text, useInput } from "ink";
import TextInput from "ink-text-input";
import type { GatewayClient } from "../api/client.js";
import type { ChatMessage, UseChat } from "../hooks/useChat.js";
import { appendHistory, loadHistory } from "../history.js";
import { StepTrace } from "./StepTrace.js";
import { Thinking } from "./Thinking.js";
import { Welcome } from "./Welcome.js";

/** A completed conversation turn, rendered once into <Static> and never again. */
function Turn({ m }: { m: ChatMessage }) {
  return (
    <Box flexDirection="column" paddingX={1} marginTop={1}>
      {m.role === "user" ? (
        <>
          <Text color="blueBright" bold>
            ❯ you
          </Text>
          <Text>{m.text}</Text>
        </>
      ) : (
        <>
          {m.steps && m.steps.length ? (
            <Box flexDirection="column">
              <Text dimColor>· thinking</Text>
              <Box marginLeft={1}>
                <StepTrace steps={m.steps} />
              </Box>
            </Box>
          ) : null}
          <Text color="magenta" bold>
            ◆ assistant
          </Text>
          {m.text ? (
            <Text>{m.text}</Text>
          ) : (
            <Text dimColor>(no reply — {m.reason ?? "the agent didn't answer"})</Text>
          )}
        </>
      )}
    </Box>
  );
}

/** The chat view: a scrollback of finalized turns (top → down) plus a live,
 * bottom-pinned region for the streaming answer and the input box. */
export function Chat({
  client,
  model,
  provider,
  onModelChange,
  onProviderChange,
  chat,
}: {
  client: GatewayClient;
  model?: string;
  provider?: string;
  onModelChange?: (model: string) => void;
  onProviderChange?: (provider: string) => void;
  /** Chat thread state, owned by App so it survives view switches and so App's
   *  height policy and this component's layout branch flip in the SAME render
   *  (a callback would lag one render behind and strand transition frames). */
  chat: UseChat;
}) {
  const { status, error, messages, pending, send } = chat;
  const [input, setInput] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const busy = status === "thinking";
  const reconnecting = status === "reconnecting";

  // On a fresh session (no turns yet) we render a full-height layout with the
  // input pinned to the bottom of the terminal — a proper "app" launch screen.
  // The moment the first message is sent the transcript takes over via <Static>
  // and the input follows the content (flicker-free scrollback). `send` appends
  // the user turn synchronously, so `messages.length` flips this the instant a
  // message is submitted — and App reads the same `messages` to drop its fixed
  // height in that same render, so the two never disagree.
  const started = messages.length > 0;

  // Shell-style prompt history: ↑/↓ recall previous inputs, persisted across
  // sessions. `histPos` = index into history while browsing (null = live draft,
  // which we stash so ↓ past the newest restores what was being typed). The
  // transcript itself scrolls with the terminal's own scrollback now that
  // finalized turns render via <Static>, so there is no in-app scroll to manage.
  const [history, setHistory] = useState<string[]>(() => loadHistory());
  const histPos = useRef<number | null>(null);
  const draft = useRef("");

  useInput((_i, key) => {
    if (history.length === 0) return;
    if (key.upArrow) {
      if (histPos.current === null) {
        draft.current = input;
        histPos.current = history.length - 1;
      } else {
        histPos.current = Math.max(0, histPos.current - 1);
      }
      setInput(history[histPos.current]);
    } else if (key.downArrow && histPos.current !== null) {
      if (histPos.current >= history.length - 1) {
        histPos.current = null;
        setInput(draft.current);
      } else {
        histPos.current += 1;
        setInput(history[histPos.current]);
      }
    }
  });

  // Typing exits history-browsing so edits start a fresh draft.
  const onInputChange = (value: string) => {
    histPos.current = null;
    setInput(value);
  };

  /** Handle in-app slash commands; returns true if the input was a command. */
  const handleSlash = (value: string): boolean => {
    if (!value.startsWith("/")) return false;
    const [cmd, ...args] = value.slice(1).trim().split(/\s+/);
    const arg = args.join(" ");
    switch (cmd) {
      case "model":
        if (arg) {
          onModelChange?.(arg);
          setNotice(`model → ${arg}`);
        } else {
          setNotice(`model: ${model ?? "default"}`);
        }
        return true;
      case "provider":
        if (arg) {
          onProviderChange?.(arg);
          setNotice(`provider → ${arg}`);
        } else {
          setNotice(`provider: ${provider ?? "default"}`);
        }
        return true;
      case "help":
        setNotice("/model <slug> · /provider <id> · /help · Esc quit");
        return true;
      default:
        setNotice(`unknown command: /${cmd} (try /help)`);
        return true;
    }
  };

  const onSubmit = (value: string) => {
    if (!value.trim() || busy) return;
    setNotice(null);
    // Record every submitted input (messages and slash commands), skipping an
    // immediate duplicate; reset the history cursor.
    setHistory((h) => (h[h.length - 1] === value ? h : [...h, value]));
    appendHistory(value);
    histPos.current = null;
    draft.current = "";
    if (handleSlash(value)) {
      setInput("");
      return;
    }
    send(value);
    setInput("");
  };

  const placeholder =
    status === "connecting"
      ? "connecting…"
      : reconnecting
        ? "reconnecting…"
        : "message  (/model <slug> · Esc quit)";

  // <Static> content: the Welcome splash first (so it sits at the very top of
  // the session) followed by each finalized turn. Ink commits these to the
  // terminal exactly once and never repaints them, so a keystroke can only ever
  // re-render the live input box below — the transcript can't flicker.
  type Row = { key: string; welcome?: true; m?: ChatMessage };
  const rows: Row[] = [
    { key: "welcome", welcome: true },
    ...messages.map((m, i) => ({ key: `m${i}`, m })),
  ];

  // The live region — the only part that repaints during a turn: the streaming
  // in-flight answer, transient banners, and the input box. Shared by both the
  // launch layout and the Static transcript layout.
  const liveRegion = (
    <>
      {/* Live in-flight turn: streams here, then moves into <Static> on
          completion. */}
      {pending ? (
        <Box flexDirection="column" paddingX={1} marginTop={1}>
          {pending.steps.length ? (
            <Box flexDirection="column">
              <Text dimColor>· thinking</Text>
              <Box marginLeft={1}>
                <StepTrace steps={pending.steps} />
              </Box>
            </Box>
          ) : null}
          {pending.text ? (
            <>
              <Text color="magenta" bold>
                ◆ assistant
              </Text>
              <Text>
                {pending.text}
                {busy ? <Text color="yellow">▌</Text> : null}
              </Text>
            </>
          ) : busy ? (
            <Thinking />
          ) : null}
        </Box>
      ) : null}

      {reconnecting ? (
        <Box paddingX={1}>
          <Thinking label="reconnecting to gateway" />
        </Box>
      ) : null}
      {error && !reconnecting ? (
        <Box paddingX={1}>
          <Text color="red">error: {error}</Text>
        </Box>
      ) : null}
      {notice ? (
        <Box paddingX={1}>
          <Text color="cyan">{notice}</Text>
        </Box>
      ) : null}

      {/* Input pinned to the bottom of the live region. */}
      <Box marginX={1} borderStyle="round" borderColor={busy || reconnecting ? "yellow" : "blue"} paddingX={1}>
        <Text color={busy || reconnecting ? "yellow" : "blue"}>{busy ? "… " : "› "}</Text>
        <TextInput
          value={input}
          onChange={onInputChange}
          onSubmit={onSubmit}
          placeholder={placeholder}
        />
      </Box>
    </>
  );

  // Launch layout: no turns yet. Welcome at the top, a flexible spacer, then the
  // input pinned to the bottom of the (fixed-height) terminal — App gives chat a
  // fixed height while empty so this fills the screen. Welcome renders directly
  // (not via <Static>) here; it moves into the scrollback the moment a turn
  // lands and we switch to the transcript layout below.
  if (!started) {
    return (
      <Box flexDirection="column" flexGrow={1}>
        <Welcome gatewayUrl={client.baseUrl()} />
        <Box flexGrow={1} />
        {liveRegion}
      </Box>
    );
  }

  // Transcript layout: finalized turns live in <Static> (native scrollback,
  // never repainted), the live region follows the content below them.
  return (
    <Box flexDirection="column" flexGrow={1}>
      <Static items={rows}>
        {(row) =>
          row.welcome ? (
            <Welcome key={row.key} gatewayUrl={client.baseUrl()} />
          ) : (
            <Turn key={row.key} m={row.m as ChatMessage} />
          )
        }
      </Static>
      {liveRegion}
    </Box>
  );
}
