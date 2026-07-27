import { useRef, useState } from "react";
import { Box, Static, Text, useInput, useStdout } from "ink";
import TextInput from "ink-text-input";
import type { GatewayClient } from "../api/client.js";
import { useChat } from "../hooks/useChat.js";
import { appendHistory, loadHistory } from "../history.js";
import { StepTrace } from "./StepTrace.js";
import { Thinking } from "./Thinking.js";
import { Welcome } from "./Welcome.js";

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

  // Shell-style prompt history: ↑/↓ recall previous inputs, persisted across
  // sessions. `histPos` = index into history while browsing (null = live draft,
  // which we stash so ↓ past the newest restores what was being typed).
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

  const reconnecting = status === "reconnecting";
  const placeholder =
    status === "connecting"
      ? "connecting…"
      : reconnecting
        ? "reconnecting…"
        : "message  (/model <slug> · Esc quit)";

  return (
    <Box flexDirection="column">
      {/* Committed transcript: each turn is printed once via <Static> and flows
          up into terminal scrollback — never repainted, so the input stays
          pinned at the bottom and streaming can't flicker the history. */}
      <Static items={messages}>
        {(m, i) => (
          <Box key={i} flexDirection="column" paddingX={1} marginBottom={1}>
            {i > 0 ? <Divider /> : null}
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
        )}
      </Static>

      {/* Live region pinned at the bottom: welcome (empty), the in-progress
          turn, and the bordered input box. */}
      <Box flexDirection="column" paddingX={1}>
        {messages.length === 0 && !pending ? <Welcome gatewayUrl={client.baseUrl()} /> : null}

        {pending ? (
          <Box flexDirection="column" marginBottom={1}>
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

        {reconnecting ? <Thinking label="reconnecting to gateway" /> : null}
        {error && !reconnecting ? <Text color="red">error: {error}</Text> : null}
        {notice ? <Text color="cyan">{notice}</Text> : null}

        <Box borderStyle="round" borderColor={busy || reconnecting ? "yellow" : "blue"} paddingX={1}>
          <Text color={busy || reconnecting ? "yellow" : "blue"}>{busy ? "… " : "› "}</Text>
          <TextInput
            value={input}
            onChange={onInputChange}
            onSubmit={onSubmit}
            placeholder={placeholder}
          />
        </Box>
      </Box>
    </Box>
  );
}
