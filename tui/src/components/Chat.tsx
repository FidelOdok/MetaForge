import { useEffect, useRef, useState } from "react";
import { Box, Text, useInput, useStdout } from "ink";
import TextInput from "ink-text-input";
import type { GatewayClient } from "../api/client.js";
import { useChat, type ChatMessage } from "../hooks/useChat.js";
import { useTerminalSize } from "../hooks/useTerminalSize.js";
import { appendHistory, loadHistory } from "../history.js";
import { StepTrace } from "./StepTrace.js";
import { Thinking } from "./Thinking.js";
import { Welcome } from "./Welcome.js";

/** Estimated rendered height (in terminal rows) of one committed turn, so the
 * viewport can show only whole turns that fit — clipping overflow in Ink garbles
 * multi-line content, so we never overflow in the first place. */
function turnHeight(m: ChatMessage, cols: number): number {
  const w = Math.max(20, cols - 4);
  const body = m.text || "(no reply — the agent didn't answer)";
  const textLines = body
    .split("\n")
    .reduce((n, line) => n + Math.max(1, Math.ceil(line.length / w)), 0);
  const stepLines = m.role === "assistant" && m.steps?.length ? m.steps.length + 1 : 0;
  return 1 /* label */ + textLines + stepLines + 1 /* divider */ + 1 /* margin */;
}

/** Full-width dim rule separating conversation turns. */
function Divider() {
  const { stdout } = useStdout();
  const width = Math.min((stdout?.columns ?? 80) - 2, 64);
  return <Text dimColor>{"─".repeat(Math.max(8, width))}</Text>;
}

/** The chat view: streaming assistant answers + tool-call trace + input. */
export function Chat({
  client,
  model,
  provider,
  onModelChange,
  onProviderChange,
}: {
  client: GatewayClient;
  model?: string;
  provider?: string;
  onModelChange?: (model: string) => void;
  onProviderChange?: (provider: string) => void;
}) {
  const { status, error, messages, pending, send } = useChat(client, model, provider);
  const [input, setInput] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const busy = status === "thinking";
  const { rows, cols } = useTerminalSize();

  // Shell-style prompt history: ↑/↓ recall previous inputs, persisted across
  // sessions. `histPos` = index into history while browsing (null = live draft,
  // which we stash so ↓ past the newest restores what was being typed).
  const [history, setHistory] = useState<string[]>(() => loadHistory());
  const histPos = useRef<number | null>(null);
  const draft = useRef("");

  // Transcript scroll: 0 = pinned to the latest (bottom). >0 = scrolled up that
  // many messages. Keep the view stable when new turns arrive while scrolled up.
  const [scrollOffset, setScrollOffset] = useState(0);
  const prevLen = useRef(0);
  useEffect(() => {
    const added = messages.length - prevLen.current;
    prevLen.current = messages.length;
    if (added > 0 && scrollOffset > 0) setScrollOffset((o) => o + added);
  }, [messages.length, scrollOffset]);
  const jumpToBottom = () => setScrollOffset(0);

  useInput((_i, key) => {
    if (key.pageUp) {
      setScrollOffset((o) => Math.min(messages.length, o + 5));
      return;
    }
    if (key.pageDown) {
      setScrollOffset((o) => Math.max(0, o - 5));
      return;
    }
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

  // Typing exits history-browsing so edits start a fresh draft, and snaps the
  // transcript back to the latest.
  const onInputChange = (value: string) => {
    histPos.current = null;
    if (scrollOffset !== 0) jumpToBottom();
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
    jumpToBottom();
    if (handleSlash(value)) {
      setInput("");
      return;
    }
    send(value);
    setInput("");
  };

  const reconnecting = status === "reconnecting";
  const placeholder =
    status === "connecting"
      ? "connecting…"
      : reconnecting
        ? "reconnecting…"
        : "message  (/model <slug> · Esc quit)";

  // Fit whole turns to the viewport (rows minus input/footer/hint chrome),
  // taking from the newest end back. Rendering only what fits means the
  // bottom-aligned viewport never overflows, so nothing gets clip-garbled.
  const atBottom = scrollOffset === 0;
  const end = messages.length - scrollOffset;
  const pendingHeight = atBottom && pending ? (pending.text ? turnHeight(pending as unknown as ChatMessage, cols) : 2 + (pending.steps.length ? pending.steps.length + 1 : 0)) : 0;
  const chrome = 3 /* input box */ + 2 /* footer */ + (atBottom ? 0 : 1) /* hint */ + (reconnecting ? 1 : 0) + (error && !reconnecting ? 1 : 0) + (notice ? 1 : 0);
  let budget = Math.max(4, rows - chrome - 1) - pendingHeight;
  let startAbs = end;
  for (let i = end - 1; i >= 0; i--) {
    const h = turnHeight(messages[i], cols);
    if (startAbs < end && budget - h < 0) break;
    budget -= h;
    startAbs = i;
  }
  const shown = messages.slice(startAbs, end);

  return (
    <Box flexDirection="column" flexGrow={1}>
      {/* Scrollable transcript viewport: fills the height, latest pinned to the
          bottom, overflow clipped off the top. */}
      <Box flexGrow={1} flexDirection="column" justifyContent="flex-end" overflow="hidden">
        {messages.length === 0 && !pending ? <Welcome gatewayUrl={client.baseUrl()} /> : null}

        {shown.map((m, i) => (
          <Box key={startAbs + i} flexDirection="column" paddingX={1} marginBottom={1}>
            {startAbs + i > 0 ? <Divider /> : null}
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
        ))}

        {atBottom && pending ? (
          <Box flexDirection="column" paddingX={1} marginBottom={1}>
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
      </Box>

      {/* Jump-to-bottom affordance when scrolled up. */}
      {!atBottom ? (
        <Box paddingX={1}>
          <Text color="cyan">▼ scrolled up — PageDown or type to jump to latest</Text>
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

      {/* Input pinned to the bottom of the view area. */}
      <Box marginX={1} borderStyle="round" borderColor={busy || reconnecting ? "yellow" : "blue"} paddingX={1}>
        <Text color={busy || reconnecting ? "yellow" : "blue"}>{busy ? "… " : "› "}</Text>
        <TextInput
          value={input}
          onChange={onInputChange}
          onSubmit={onSubmit}
          placeholder={placeholder}
        />
      </Box>
    </Box>
  );
}
