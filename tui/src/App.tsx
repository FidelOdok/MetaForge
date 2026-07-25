import { useEffect, useState } from "react";
import { Box, Text, useApp, useInput } from "ink";
import { loadConfig } from "./config.js";
import { GatewayClient } from "./api/client.js";
import { StatusBar } from "./components/StatusBar.js";
import { Chat } from "./components/Chat.js";

/**
 * Root TUI: header + status bar + chat view. Iteration 2 adds streaming chat
 * over /v1/chat with the tool-call trace. Gated-run approval lands next.
 */
export function App() {
  const { exit } = useApp();
  const cfg = loadConfig();
  const [client] = useState(() => new GatewayClient(cfg));
  const [health, setHealth] = useState("checking…");
  const [project, setProject] = useState<string | undefined>(undefined);

  useInput((input, key) => {
    if (key.escape || (key.ctrl && input === "c")) exit();
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
      <Box paddingX={1}>
        <Text bold color="magenta">
          MetaForge
        </Text>
        <Text dimColor> · gated design-flow harness</Text>
      </Box>

      <StatusBar
        gatewayUrl={cfg.gateway_url}
        health={health}
        model={cfg.model}
        mode={cfg.mode}
        project={project}
      />

      <Box marginTop={1}>
        <Chat client={client} model={cfg.model} provider={cfg.provider} />
      </Box>
    </Box>
  );
}
