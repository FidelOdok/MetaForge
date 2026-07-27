import { useEffect, useState } from "react";
import { Box, Text, useApp, useInput } from "ink";
import { loadConfig, setConfigValue } from "./config.js";
import { GatewayClient } from "./api/client.js";
import { Chat } from "./components/Chat.js";
import { RunsView } from "./components/RunsView.js";
import { IntentForm } from "./components/IntentForm.js";
import { TwinView } from "./components/TwinView.js";
import { useRunAlerts } from "./hooks/useRunAlerts.js";

type View = "chat" | "runs" | "new" | "twin";

const ALERT_COLOR = { gate: "yellow", done: "green", failed: "red" } as const;

/**
 * Root TUI: header + status bar + a switchable view. Ctrl+T = chat (streaming
 * assistant), Ctrl+R = runs (gated design-flow timeline + approvals).
 */
export function App() {
  const { exit } = useApp();
  const cfg = loadConfig();
  const [client] = useState(() => new GatewayClient(cfg));
  const [health, setHealth] = useState("checking…");
  const [project, setProject] = useState<string | undefined>(undefined);
  const [view, setView] = useState<View>("chat");
  const [model, setModel] = useState<string | undefined>(cfg.model);
  const [provider, setProvider] = useState<string | undefined>(cfg.provider);
  const { awaiting, alert } = useRunAlerts(client);

  // Change the session model/provider live and persist it (mirrors `forge
  // config set`), so /model in chat sticks across launches too.
  const changeModel = (m: string) => {
    setModel(m);
    try {
      setConfigValue("model", m);
    } catch {
      /* best-effort persist */
    }
  };
  const changeProvider = (p: string) => {
    setProvider(p);
    try {
      setConfigValue("provider", p);
    } catch {
      /* best-effort persist */
    }
  };

  useInput((input, key) => {
    if (key.ctrl && input === "c") {
      exit();
      return;
    }
    if (key.ctrl && input === "r") setView("runs");
    else if (key.ctrl && input === "t") setView("chat");
    else if (key.ctrl && input === "n") setView("new");
    else if (key.ctrl && input === "b") setView("twin");
    else if (key.escape && view === "chat") exit();
  });

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const h = await client.health();
        if (alive) setHealth(h.status);
        const projects = await client.listProjects();
        if (alive) setProject(projects[0]?.name);
      } catch {
        if (alive) setHealth("unreachable");
      }
    })();
    return () => {
      alive = false;
    };
  }, [client]);

  const healthColor = health === "healthy" ? "green" : health === "checking…" ? "yellow" : "red";
  const gateway = cfg.gateway_url.replace(/^https?:\/\//, "");

  return (
    <Box flexDirection="column">
      {alert && (
        <Box paddingX={1}>
          <Text color={ALERT_COLOR[alert.kind]}>
            {alert.kind === "gate" ? "🔔 " : alert.kind === "done" ? "✓ " : "✗ "}
            {alert.text}
            {alert.kind === "gate" ? <Text dimColor> — ^R to review</Text> : null}
          </Text>
        </Box>
      )}

      {/* View content flows up; the status/nav footer stays pinned at the bottom
          (with the chat transcript in <Static> above it). */}
      <Box flexDirection="column">
        {view === "chat" && (
          <Chat
            client={client}
            model={model}
            provider={provider}
            onModelChange={changeModel}
            onProviderChange={changeProvider}
          />
        )}
        {view === "runs" && <RunsView client={client} onExit={() => setView("chat")} />}
        {view === "new" && (
          <IntentForm
            client={client}
            onCreated={() => setView("runs")}
            onCancel={() => setView("chat")}
          />
        )}
        {view === "twin" && <TwinView client={client} onExit={() => setView("chat")} />}
      </Box>

      <Box paddingX={1} justifyContent="space-between">
        <Text dimColor>
          <Text color={healthColor}>● {health}</Text> · {gateway} · {project ?? "no project"} ·{" "}
          {model ?? "default"} · {cfg.mode}
        </Text>
        <Text dimColor>
          {awaiting > 0 ? (
            <Text color="yellow" bold>
              ⏸ {awaiting}
              {"  "}
            </Text>
          ) : null}
          <Text color={view === "chat" ? "cyan" : undefined}>chat</Text>
          {" · "}
          <Text color={view === "runs" ? "cyan" : undefined}>runs</Text>
          {" · "}
          <Text color={view === "new" ? "cyan" : undefined}>new</Text>
          {" · "}
          <Text color={view === "twin" ? "cyan" : undefined}>twin</Text>
          {"  ^T/^R/^N/^B"}
        </Text>
      </Box>
    </Box>
  );
}
