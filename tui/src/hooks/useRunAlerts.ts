import { useEffect, useRef, useState } from "react";
import type { GatewayClient } from "../api/client.js";

export interface RunAlert {
  runId: string;
  kind: "gate" | "done" | "failed";
  text: string;
}

export interface RunAlerts {
  /** How many runs are currently paused at a gate. */
  awaiting: number;
  /** The most recent transition alert (auto-clears), or null. */
  alert: RunAlert | null;
}

const SHORT = (id: string) => id.replace("run_", "").slice(0, 8);

/**
 * Polls the runs list app-wide and raises an alert on meaningful transitions —
 * a run entering `awaiting_approval` (a gate needs you) or reaching a terminal
 * state — so the header can badge it even when you're not on the runs view.
 */
export function useRunAlerts(client: GatewayClient, pollMs = 4000): RunAlerts {
  const [awaiting, setAwaiting] = useState(0);
  const [alert, setAlert] = useState<RunAlert | null>(null);
  const prev = useRef<Map<string, string>>(new Map());
  const seeded = useRef(false);

  useEffect(() => {
    let alive = true;
    let clearTimer: ReturnType<typeof setTimeout> | undefined;

    const raise = (a: RunAlert) => {
      if (!alive) return;
      setAlert(a);
      if (clearTimer) clearTimeout(clearTimer);
      clearTimer = setTimeout(() => alive && setAlert(null), 7000);
    };

    const tick = async () => {
      let runs;
      try {
        runs = await client.listRuns();
      } catch {
        return;
      }
      if (!alive) return;
      let count = 0;
      for (const r of runs) {
        if (r.status === "awaiting_approval") count += 1;
        const before = prev.current.get(r.id);
        // Only alert on an observed transition (skip the first seed pass so we
        // don't shout about pre-existing runs on startup).
        if (seeded.current && before !== undefined && before !== r.status) {
          if (r.status === "awaiting_approval") {
            raise({ runId: r.id, kind: "gate", text: `Run ${SHORT(r.id)} awaiting approval` });
          } else if (r.status === "completed") {
            raise({ runId: r.id, kind: "done", text: `Run ${SHORT(r.id)} completed` });
          } else if (r.status === "failed" || r.status === "rejected") {
            raise({ runId: r.id, kind: "failed", text: `Run ${SHORT(r.id)} ${r.status}` });
          }
        }
        prev.current.set(r.id, r.status);
      }
      setAwaiting(count);
      seeded.current = true;
    };

    void tick();
    const t = setInterval(() => void tick(), pollMs);
    return () => {
      alive = false;
      clearInterval(t);
      if (clearTimer) clearTimeout(clearTimer);
    };
  }, [client, pollMs]);

  return { awaiting, alert };
}
