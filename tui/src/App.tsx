import { useEffect, useState } from "react";
import { Box, Text, useApp, useInput } from "ink";
import { loadConfig } from "./config.js";
import { GatewayClient } from "./api/client.js";
import { StatusBar } from "./components/StatusBar.js";
import { Chat } from "./components/Chat.js";
import { RunsView } from "./components/RunsView.js";

type View = "chat" | "runs";

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

  useInput((input, key) => {
    if (key.ctrl && input === "c") {
      exit();
      return;
    }
    if (key.ctrl && input === "r") setView("runs");
    else if (key.ctrl && input === "t") setView("chat");
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

  return (
    <Box flexDirection="column">
      <Box paddingX={1} justifyContent="space-between">
        <Box>
          <Text bold color="magenta">
            MetaForge
          </Text>
          <Text dimColor> · gated design-flow harness</Text>
        </Box>
        <Text dimColor>
          <Text color={view === "chat" ? "cyan" : undefined}>chat</Text>
          {" · "}
          <Text color={view === "runs" ? "cyan" : undefined}>runs</Text>
          {"  ^T/^R"}
        </Text>
      </Box>

      <StatusBar
        gatewayUrl={cfg.gateway_url}
        health={health}
        model={cfg.model}
        mode={cfg.mode}
        project={project}
      />

      <Box marginTop={1}>
        {view === "chat" ? (
          <Chat client={client} model={cfg.model} provider={cfg.provider} />
        ) : (
          <RunsView client={client} onExit={() => setView("chat")} />
        )}
      </Box>
    </Box>
  );
}
