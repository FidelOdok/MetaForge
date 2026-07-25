import { useEffect, useState } from "react";
import { Box, Text, useApp, useInput } from "ink";
import { loadConfig } from "./config.js";
import { GatewayClient, type Project } from "./api/client.js";
import { StatusBar } from "./components/StatusBar.js";

/**
 * Root TUI screen (iteration 1): header, live gateway health + project list,
 * status bar. Chat streaming and the gated-run approval flow land next.
 */
export function App() {
  const { exit } = useApp();
  const cfg = loadConfig();
  const [client] = useState(() => new GatewayClient(cfg));
  const [health, setHealth] = useState("checking…");
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState<string | null>(null);

  useInput((input, key) => {
    if (input === "q" || key.escape || (key.ctrl && input === "c")) exit();
  });

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const h = await client.health();
        if (alive) setHealth(h.status);
      } catch (e) {
        if (alive) {
          setHealth("unreachable");
          setError((e as Error).message);
        }
        return;
      }
      try {
        const p = await client.listProjects();
        if (alive) setProjects(p);
      } catch (e) {
        if (alive) setError((e as Error).message);
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
        project={projects[0]?.name}
      />

      <Box flexDirection="column" paddingX={1} marginTop={1}>
        <Text bold>Projects ({projects.length})</Text>
        {error && <Text color="red">  error: {error}</Text>}
        {projects.slice(0, 8).map((p) => (
          <Text key={p.id}>
            {"  • "}
            {p.name}{" "}
            <Text dimColor>
              [{p.status}] — {p.work_products?.length ?? 0} work products
            </Text>
          </Text>
        ))}
        {!error && projects.length === 0 && <Text dimColor>  (loading…)</Text>}
      </Box>

      <Box paddingX={1} marginTop={1}>
        <Text dimColor>q / Esc to quit — chat &amp; gated runs land next</Text>
      </Box>
    </Box>
  );
}
