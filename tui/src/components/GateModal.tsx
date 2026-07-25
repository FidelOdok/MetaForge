import { Box, Text } from "ink";

/**
 * Approval prompt shown when a run is paused at a gate. The `reason` is the
 * gate's `approval_reason` (phase summary + review criteria + deliverable
 * readiness); [a] approves, [x] rejects.
 */
export function GateModal({ reason, busy }: { reason: string; busy?: boolean }) {
  const ready = /missing:\s*none/i.test(reason);
  return (
    <Box
      flexDirection="column"
      borderStyle="double"
      borderColor="yellow"
      paddingX={1}
      marginTop={1}
    >
      <Text bold color="yellow">
        ⏸ Gate — approval required{" "}
        {reason ? (
          <Text color={ready ? "green" : "red"}>
            ({ready ? "deliverables present" : "deliverables missing"})
          </Text>
        ) : null}
      </Text>
      <Box marginTop={1}>
        <Text wrap="wrap">{reason || "(no reason provided)"}</Text>
      </Box>
      <Box marginTop={1}>
        {busy ? (
          <Text color="yellow">submitting…</Text>
        ) : (
          <Text>
            <Text color="green">[a] approve</Text>
            {"   "}
            <Text color="red">[x] reject</Text>
            {"   "}
            <Text dimColor>[Esc] back</Text>
          </Text>
        )}
      </Box>
    </Box>
  );
}

const STATUS_COLOR: Record<string, string> = {
  completed: "green",
  running: "cyan",
  awaiting_approval: "yellow",
  failed: "red",
  rejected: "red",
  canceled: "gray",
  queued: "gray",
};

export function statusColor(status: string | undefined): string {
  return (status && STATUS_COLOR[status]) || "white";
}
