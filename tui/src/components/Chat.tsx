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
}: {
  client: GatewayClient;
  model?: string;
  provider?: string;
}) {
  const { status, error, messages, pending, send } = useChat(client, model, provider);
  const [input, setInput] = useState("");
  const busy = status === "thinking";

  const onSubmit = (value: string) => {
    if (!value.trim() || busy) return;
    send(value);
    setInput("");
  };

  return (
    <Box flexDirection="column" paddingX={1}>
      {messages.map((m, i) => (
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
                {m.text}
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

      <Box>
        <Text color={busy ? "yellow" : "blue"}>{busy ? "… " : "› "}</Text>
        <TextInput
          value={input}
          onChange={setInput}
          onSubmit={onSubmit}
          placeholder={
            status === "connecting" ? "connecting…" : "message (Esc to quit)"
          }
        />
      </Box>
    </Box>
  );
}
