import { Box, Text, useStdout } from "ink";
import { MISSION, TAGLINE, TUI_HINTS, WORDMARK } from "../banner.js";

/**
 * First-impression splash shown at the top of an empty chat: brand wordmark,
 * what MetaForge does, and how to get moving. Replaced by the conversation once
 * the first message is sent. Falls back to a compact title on narrow terminals.
 */
export function Welcome({ gatewayUrl }: { gatewayUrl?: string }) {
  const { stdout } = useStdout();
  const wide = (stdout?.columns ?? 80) >= 46;

  return (
    <Box flexDirection="column" marginBottom={1}>
      {wide ? (
        WORDMARK.map((line, i) => (
          <Text key={i} color="yellow" bold>
            {line}
          </Text>
        ))
      ) : (
        <Text color="yellow" bold>
          ◆ forge
        </Text>
      )}
      <Text color="cyan">{TAGLINE}</Text>

      <Box flexDirection="column" marginTop={1}>
        {MISSION.map((line, i) => (
          <Text key={i} dimColor>
            {line}
          </Text>
        ))}
      </Box>

      <Box flexDirection="column" marginTop={1}>
        {TUI_HINTS.map(([key, desc], i) => (
          <Text key={i}>
            <Text color="green">{key.padEnd(18)}</Text>
            <Text dimColor>{desc}</Text>
          </Text>
        ))}
      </Box>

      {gatewayUrl ? (
        <Box marginTop={1}>
          <Text dimColor>gateway {gatewayUrl}</Text>
        </Box>
      ) : null}
    </Box>
  );
}
