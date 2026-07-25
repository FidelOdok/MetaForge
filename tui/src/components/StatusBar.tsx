import { Box, Text } from "ink";

export interface StatusBarProps {
  gatewayUrl: string;
  health: string;
  model?: string;
  mode?: string;
  project?: string;
}

/** Bottom status line: gateway health, active project, model, mode. */
export function StatusBar({ gatewayUrl, health, model, mode, project }: StatusBarProps) {
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
        <Text>{project ?? "—"}</Text>
        <Text dimColor>  model </Text>
        <Text>{model ?? "default"}</Text>
        <Text dimColor>  mode </Text>
        <Text color="cyan">{mode ?? "ask"}</Text>
      </Box>
    </Box>
  );
}
