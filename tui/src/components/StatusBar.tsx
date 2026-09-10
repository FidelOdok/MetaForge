import { Box, Text } from "ink";
import { scopeLabel, type ChatScope } from "../lib/project.js";

export interface StatusBarProps {
  gatewayUrl: string;
  health: string;
  model?: string;
  mode?: string;
  /** Scope of the live chat thread (`null` = no thread yet). */
  scope?: ChatScope | null;
}

/**
 * Single-line status: gateway health, the chat thread's scope, model, mode.
 *
 * The scope segment renders from the thread that actually exists — passing a
 * project name that isn't the thread's scope would tell the user their work is
 * landing somewhere it isn't, so this takes a `ChatScope`, not a display string.
 */
export function StatusBar({ gatewayUrl, health, model, mode, scope = null }: StatusBarProps) {
  const ok = health === "healthy";
  return (
    <Box
      borderStyle="round"
      borderColor="gray"
      paddingX={1}
      justifyContent="space-between"
    >
      <Box>
        <Text color={ok ? "green" : health === "checking…" ? "yellow" : "red"}>
          {ok ? "● " : "○ "}
        </Text>
        <Text dimColor>{gatewayUrl}</Text>
        <Text dimColor> ({health})</Text>
      </Box>
      <Box>
        <Text dimColor>project </Text>
        <Text>{scopeLabel(scope)}</Text>
        <Text dimColor>  model </Text>
        <Text>{model ?? "default"}</Text>
        <Text dimColor>  mode </Text>
        <Text color="cyan">{mode ?? "ask"}</Text>
      </Box>
    </Box>
  );
}
