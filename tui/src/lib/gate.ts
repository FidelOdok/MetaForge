/** Pure gate helpers, extracted so they're testable without rendering Ink. */

/** A gate is "ready" when its reason reports no missing deliverables. */
export function gateReady(reason: string): boolean {
  return /missing:\s*none/i.test(reason);
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
