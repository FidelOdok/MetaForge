import { useState } from "react";
import { Box, Text, useStdout } from "ink";
import TextInput from "ink-text-input";
import type { GatewayClient } from "../api/client.js";
import { useChat } from "../hooks/useChat.js";
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
    if (handleSlash(value)) {
      setInput("");
      return;
    }
    send(value);
    setInput("");
  };

  // Cap scrollback so a long session doesn't overflow the terminal.
  const MAX_VISIBLE = 8;
  const visible = messages.slice(-MAX_VISIBLE);
  const hidden = messages.length - visible.length;

  const reconnecting = status === "reconnecting";
  const placeholder =
    status === "connecting"
      ? "connecting…"
      : reconnecting
        ? "reconnecting…"
        : "message  (/model <slug> · Esc quit)";

  return (
    <Box flexDirection="column" paddingX={1}>
      {messages.length === 0 && !pending ? <Welcome gatewayUrl={client.baseUrl()} /> : null}
      {hidden > 0 && <Text dimColor>… {hidden} earlier message{hidden > 1 ? "s" : ""}</Text>}
      {visible.map((m, i) => (
        <Box key={i} flexDirection="column" marginBottom={1}>
          {i > 0 || hidden > 0 ? <Divider /> : null}
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

      {pending ? (
        <Box flexDirection="column" marginBottom={1}>
          {messages.length ? <Divider /> : null}
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
        <Box>
          <Thinking label="reconnecting to gateway" />
        </Box>
      ) : null}
      {error && !reconnecting ? <Text color="red">error: {error}</Text> : null}
      {notice ? <Text color="cyan">{notice}</Text> : null}

      <Box>
        <Text color={busy ? "yellow" : reconnecting ? "yellow" : "blue"}>{busy ? "… " : "› "}</Text>
        <TextInput value={input} onChange={setInput} onSubmit={onSubmit} placeholder={placeholder} />
      </Box>
    </Box>
  );
}
