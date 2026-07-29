import { useEffect, useState } from "react";
import { Box, Text, useApp, useInput } from "ink";
import { loadConfig, setConfigValue } from "./config.js";
import { GatewayClient } from "./api/client.js";
import { useChat } from "./hooks/useChat.js";
import { Chat } from "./components/Chat.js";
import { RunsView } from "./components/RunsView.js";
import { IntentForm } from "./components/IntentForm.js";
import { TwinView } from "./components/TwinView.js";
import { useRunAlerts } from "./hooks/useRunAlerts.js";
import { useTerminalSize } from "./hooks/useTerminalSize.js";

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
  const { rows } = useTerminalSize();

  // Own the chat thread here (not inside <Chat>) so it survives view switches —
  // ^R/^B/^N no longer tear down the SSE stream and drop the conversation.
  const chat = useChat(client, model, provider);

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

  // Height/clip policy. Chat renders its finalized turns via Ink <Static>
  // (committed to the terminal's own scrollback, above the live region), so a
  // fixed root height + overflow:hidden would clip that scrollback and strand
  // the input mid-screen — chat flows naturally instead. Its launch screen pins
  // the input to the bottom with its own sized spacer (see <Chat/>). The panel
  // views (runs/twin/new) still fill a fixed viewport.
  const isChat = view === "chat";
  const fill = !isChat;

  return (
    <Box flexDirection="column" {...(fill ? { height: rows } : {})}>
      {alert && (
        <Box paddingX={1}>
          <Text color={ALERT_COLOR[alert.kind]}>
            {alert.kind === "gate" ? "🔔 " : alert.kind === "done" ? "✓ " : "✗ "}
            {alert.text}
            {alert.kind === "gate" ? <Text dimColor> — ^R to review</Text> : null}
          </Text>
        </Box>
      )}

      {/* The view fills all remaining height; the chat input and this footer are
          the last rows, so the input is pinned to the bottom of the terminal. */}
      <Box flexGrow={1} flexDirection="column" {...(fill ? { overflow: "hidden" as const } : {})}>
        {view === "chat" && (
          <Chat
            client={client}
            model={model}
            provider={provider}
            onModelChange={changeModel}
            onProviderChange={changeProvider}
            chat={chat}
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

      {/* Two stacked, truncating lines so a long project/model name never
          collides the way a single space-between row did. */}
      <Box flexDirection="column" paddingX={1}>
        <Text dimColor wrap="truncate">
          <Text color={healthColor}>● {health}</Text> · {gateway} · {project ?? "no project"} ·{" "}
          {model ?? "default"} · {cfg.mode}
        </Text>
        <Text dimColor wrap="truncate">
          {awaiting > 0 ? (
            <Text color="yellow" bold>
              ⏸ {awaiting} gate{awaiting > 1 ? "s" : ""} ·{" "}
            </Text>
          ) : null}
          <Text color={view === "chat" ? "cyan" : undefined}>chat</Text>
          {" · "}
          <Text color={view === "runs" ? "cyan" : undefined}>runs</Text>
          {" · "}
          <Text color={view === "new" ? "cyan" : undefined}>new</Text>
          {" · "}
          <Text color={view === "twin" ? "cyan" : undefined}>twin</Text>
          {"   ^T/^R/^N/^B  ·  PageUp/PageDn scroll"}
        </Text>
      </Box>
    </Box>
  );
}
