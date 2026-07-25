import { Box, Text } from "ink";
import type { AgentStep } from "../api/chat.js";

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

/** Renders ReAct steps as a tool-call timeline: ⏺ tool(args) ✓/✗ and thoughts. */
export function StepTrace({ steps }: { steps: AgentStep[] }) {
  if (!steps.length) return null;
  return (
    <Box flexDirection="column">
      {steps.map((s, i) => {
        if (s.tool) {
          const ok = !s.error;
          const args = s.arguments ? truncate(JSON.stringify(s.arguments), 56) : "";
          return (
            <Text key={i} dimColor>
              {"⏺ "}
              <Text color="cyan">{s.tool}</Text>({args}){" "}
              <Text color={ok ? "green" : "red"}>{ok ? "✓" : "✗"}</Text>
              {s.error ? <Text color="red"> {truncate(String(s.error), 48)}</Text> : null}
            </Text>
          );
        }
        if (s.thought) {
          return (
            <Text key={i} dimColor italic>
              {"  "}
              {truncate(String(s.thought), 72)}
            </Text>
          );
        }
        return null;
      })}
    </Box>
  );
}
