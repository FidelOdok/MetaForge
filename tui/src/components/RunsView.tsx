import { useEffect, useRef, useState } from "react";
import { Box, Text, useInput } from "ink";
import type { GatewayClient, Run } from "../api/client.js";
import { streamRunStatus, type RunStatusEvent } from "../api/runs.js";
import { EmptyState } from "./EmptyState.js";
import { GateModal, statusColor } from "./GateModal.js";
import { pageJump, visibleWindow, windowLabel } from "../lib/paging.js";
import { useTerminalSize } from "../hooks/useTerminalSize.js";

interface Phase {
  id: string;
  title?: string;
  status?: string;
}

/** Runs list + live-followed detail + gate approval. Esc returns to chat. */
export function RunsView({
  client,
  onExit,
}: {
  client: GatewayClient;
  onExit: () => void;
}) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [sel, setSel] = useState(0);
  const [live, setLive] = useState<RunStatusEvent | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const { rows: termRows } = useTerminalSize();
  // MET-606: selection-following window. Reserve rows for the header, the
  // detail box (goal/status + up to 7 phase rows + borders), the gate modal,
  // and App's footer, so list + detail never exceed the terminal.
  const RESERVED_ROWS = 22;
  const pageSize = Math.max(4, termRows - RESERVED_ROWS);
  const winStart = useRef(0);

  async function refresh() {
    try {
      setRuns(await client.listRuns());
      setNote(null);
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    void refresh();
    const t = setInterval(() => void refresh(), 3000);
    return () => clearInterval(t);
  }, []);

  const selected: Run | undefined = runs[sel];

  // Live-follow the selected run's status via SSE.
  useEffect(() => {
    setLive(null);
    if (!selected) return;
    const controller = new AbortController();
    void (async () => {
      try {
        for await (const ev of streamRunStatus(client.baseUrl(), selected.id, controller.signal)) {
          setLive(ev);
        }
      } catch {
        // stream ends on terminal/abort — fine
      }
    })();
    return () => controller.abort();
  }, [selected?.id, client]);

  const liveForSel = live && selected && live.id === selected.id ? live : null;
  const status = liveForSel?.status ?? selected?.status;
  const reason = liveForSel?.approval_reason ?? selected?.approval_reason ?? "";
  const awaiting = status === "awaiting_approval";

  async function decide(decision: "approve" | "reject") {
    if (!selected) return;
    setBusy(true);
    setNote(null);
    try {
      await client.submitApproval(selected.id, decision);
      setNote(`${decision} sent`);
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
      void refresh();
    }
  }

  useInput((input, key) => {
    if (key.escape) {
      onExit();
      return;
    }
    if (key.upArrow) setSel((s) => Math.max(0, s - 1));
    if (key.downArrow) setSel((s) => Math.min(Math.max(0, runs.length - 1), s + 1));
    if (key.pageUp) setSel((s) => pageJump(s, runs.length, pageSize, -1));
    if (key.pageDown) setSel((s) => pageJump(s, runs.length, pageSize, 1));
    if (awaiting && !busy) {
      if (input === "a") void decide("approve");
      if (input === "x") void decide("reject");
    }
  });

  const goal = selected ? String((selected.request?.goal as string) ?? "") : "";
  const phases = (selected?.result?.phases as Phase[] | undefined) ?? [];

  const win = visibleWindow(runs.length, sel, pageSize, winStart.current);
  winStart.current = win.start;

  return (
    <Box flexDirection="column" paddingX={1}>
      <Text bold>
        {windowLabel("Runs", win, runs.length)}{" "}
        <Text dimColor>↑/↓ select · PgUp/PgDn page · Esc chat</Text>
      </Text>

      <Box flexDirection="column" marginTop={1}>
        {!loaded && runs.length === 0 && !note ? <Text dimColor>  loading…</Text> : null}
        {loaded && runs.length === 0 ? (
          <EmptyState
            title="No runs yet."
            hints={["Press Ctrl+N to start a design-flow run", "or run:  forge runs create"]}
          />
        ) : null}
        {runs.slice(win.start, win.end).map((r, offset) => {
          const i = win.start + offset;
          const st = i === sel ? (liveForSel?.status ?? r.status) : r.status;
          const runGoal = String((r.request?.goal as string) ?? "");
          return (
            <Text key={r.id} wrap="truncate">
              <Text color={i === sel ? "cyan" : undefined}>{i === sel ? "❯ " : "  "}</Text>
              <Text dimColor>{r.id.replace("run_", "").slice(0, 10)}</Text>{" "}
              <Text color={statusColor(st)}>{st}</Text>
              {runGoal ? <Text dimColor> · {runGoal.slice(0, 48)}</Text> : null}
            </Text>
          );
        })}
      </Box>

      {selected && (
        <Box flexDirection="column" marginTop={1} borderStyle="round" borderColor="gray" paddingX={1}>
          <Text wrap="truncate">
            <Text dimColor>goal </Text>
            {goal || "—"}
          </Text>
          <Text>
            <Text dimColor>status </Text>
            <Text color={statusColor(status)}>{status}</Text>
            {selected.error ? <Text color="red"> — {selected.error}</Text> : null}
          </Text>
          {phases.length > 0 && (
            <Box flexDirection="column" marginTop={1}>
              <Text dimColor>phases</Text>
              {phases.map((p) => (
                <Text key={p.id}>
                  {"  "}
                  <Text color={statusColor(p.status)}>●</Text> {p.title ?? p.id}{" "}
                  <Text dimColor>[{p.status}]</Text>
                </Text>
              ))}
            </Box>
          )}
        </Box>
      )}

      {awaiting && <GateModal reason={reason ?? ""} busy={busy} />}
      {note && <Text dimColor>  {note}</Text>}
    </Box>
  );
}
