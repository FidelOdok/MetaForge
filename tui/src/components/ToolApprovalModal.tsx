import { Box, Text } from "ink";
import { toolApprovalArgPreview } from "../lib/transcript-height.js";

/**
 * Approval prompt shown when a chat turn pauses on a `requires_approval`
 * tool call (twin.commit_geometry, twin.record_decision,
 * project.create/update/delete -- FORGE-33). Same [a]/[x] convention as
 * <GateModal>, but the reason here is always "this specific tool call",
 * so it renders the tool name + a bounded arguments preview instead of a
 * gate's readiness prose.
 */
export function ToolApprovalModal({
  tool,
  arguments: args,
  busy,
}: {
  tool: string;
  arguments: Record<string, unknown>;
  busy?: boolean;
}) {
  const preview = toolApprovalArgPreview(args);
  return (
    <Box
      flexDirection="column"
      borderStyle="double"
      borderColor="yellow"
      paddingX={1}
      marginTop={1}
    >
      <Text bold color="yellow">
        ⏸ Approval required — {tool}
      </Text>
      <Box marginTop={1} flexDirection="column">
        {preview.map((line, i) => (
          <Text key={i} dimColor wrap="truncate-end">
            {line}
          </Text>
        ))}
      </Box>
      <Box marginTop={1}>
        {busy ? (
          <Text color="yellow">submitting…</Text>
        ) : (
          <Text>
            <Text color="green">[a] approve</Text>
            {"   "}
            <Text color="red">[x] reject</Text>
          </Text>
        )}
      </Box>
    </Box>
  );
}
