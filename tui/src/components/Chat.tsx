import { useState } from "react";
import { Box, Text } from "ink";
import TextInput from "ink-text-input";
import type { GatewayClient } from "../api/client.js";
import { useChat } from "../hooks/useChat.js";
import { StepTrace } from "./StepTrace.js";

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

  return (
    <Box flexDirection="column" paddingX={1}>
      {hidden > 0 && <Text dimColor>… {hidden} earlier message{hidden > 1 ? "s" : ""}</Text>}
      {visible.map((m, i) => (
        <Box key={i} flexDirection="column" marginBottom={1}>
          {m.role === "user" ? (
            <Text>
              <Text color="blue" bold>
                {"› "}
              </Text>
              {m.text}
            </Text>
          ) : (
            <Box flexDirection="column">
              {m.steps && m.steps.length ? <StepTrace steps={m.steps} /> : null}
              <Text>
                <Text color="magenta" bold>
                  assistant{"  "}
                </Text>
                {m.text ? (
                  m.text
                ) : (
                  <Text dimColor>(no reply — {m.reason ?? "the agent didn't answer"})</Text>
                )}
              </Text>
            </Box>
          )}
        </Box>
      ))}

      {pending ? (
        <Box flexDirection="column" marginBottom={1}>
          <StepTrace steps={pending.steps} />
          <Text>
            <Text color="magenta" bold>
              assistant{"  "}
            </Text>
            {pending.text}
            {busy ? <Text color="yellow">▌</Text> : null}
          </Text>
        </Box>
      ) : null}

      {error ? <Text color="red">error: {error}</Text> : null}
      {notice ? <Text color="cyan">{notice}</Text> : null}

      <Box>
        <Text color={busy ? "yellow" : "blue"}>{busy ? "… " : "› "}</Text>
        <TextInput
          value={input}
          onChange={setInput}
          onSubmit={onSubmit}
          placeholder={
            status === "connecting" ? "connecting…" : "message  (/model <slug> · Esc quit)"
          }
        />
      </Box>
    </Box>
  );
}
