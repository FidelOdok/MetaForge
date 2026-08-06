import { Box, Text } from "ink";
import type { ContextStats } from "../api/chat.js";

/** Compact token count: 92 → "92", 4210 → "4.2k", 1_000_000 → "1.0M". */
function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(Math.round(n));
}

const SHORT: Record<string, string> = {
  system: "sys",
  project_brief: "brief",
  history: "hist",
  tools: "tools",
  message: "msg",
};

/**
 * One-line context-window gauge fed by the gateway's `context.stats` SSE event:
 * a fill bar + used/window + per-component token breakdown. Truncates so it
 * never wraps and disturbs the pinned input above it.
 */
export function ContextMeter({ stats }: { stats: ContextStats }) {
  const pct = stats.window ? Math.round((stats.used / stats.window) * 100) : 0;
  const cells = 12;
  const filled = Math.max(0, Math.min(cells, Math.round((pct / 100) * cells)));
  const bar = "█".repeat(filled) + "░".repeat(cells - filled);
  const color = pct >= 90 ? "red" : pct >= 70 ? "yellow" : "green";
  const parts = stats.components
    .map((c) => {
      const label = SHORT[c.key] ?? c.key;
      const counts =
        c.items_available != null ? ` ${c.items_included ?? 0}/${c.items_available}` : "";
      return `${label} ${fmt(c.tokens)}${counts}`;
    })
    .join(" · ");
  // MET-596: real provider-reported turn usage lands on the final stats emit;
  // estimates keep the meter alive before/without it.
  const usage = stats.usage
    ? ` · in ${fmt(stats.usage.input_tokens)} / out ${fmt(stats.usage.output_tokens)} tok`
    : "";
  return (
    <Box paddingX={1}>
      <Text dimColor wrap="truncate">
        <Text color={color}>{bar}</Text> ctx {fmt(stats.used)}/{fmt(stats.window)} · {pct}% ·{" "}
        {parts}
        {usage}
      </Text>
    </Box>
  );
}
