import { useEffect, useState } from "react";
import { Box, Text, useInput } from "ink";
import TextInput from "ink-text-input";
import type { GatewayClient, Project } from "../api/client.js";

/**
 * Launches a gated design-flow run: pick a project, state the goal, submit.
 * This is the entry point to the elicitation the Requirements gate then
 * enforces — the goal is captured, not invented downstream.
 */
export function NewRun({
  client,
  flow = "design_v1",
  onCreated,
  onCancel,
}: {
  client: GatewayClient;
  flow?: string;
  onCreated: (runId: string) => void;
  onCancel: () => void;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [sel, setSel] = useState(0);
  const [step, setStep] = useState<"project" | "goal" | "submitting">("project");
  const [goal, setGoal] = useState("");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void client
      .listProjects()
      .then((p) => alive && setProjects(p))
      .catch((e: Error) => alive && setErr(e.message));
    return () => {
      alive = false;
    };
  }, [client]);

  async function submit() {
    const project = projects[sel];
    if (!project || !goal.trim()) return;
    setStep("submitting");
    setErr(null);
    try {
      const run = await client.createRun({
        goal: goal.trim(),
        flow,
        project_id: project.id,
      });
      onCreated(run.id);
    } catch (e) {
      setErr((e as Error).message);
      setStep("goal");
    }
  }

  useInput((_input, key) => {
    if (key.escape) {
      onCancel();
      return;
    }
    if (step === "project") {
      if (key.upArrow) setSel((s) => Math.max(0, s - 1));
      if (key.downArrow) setSel((s) => Math.min(Math.max(0, projects.length - 1), s + 1));
      if (key.return && projects[sel]) setStep("goal");
    }
  });

  const project = projects[sel];

  return (
    <Box flexDirection="column" paddingX={1}>
      <Text bold color="yellow">
        New design-flow run <Text dimColor>· flow {flow} · Esc cancel</Text>
      </Text>

      <Box flexDirection="column" marginTop={1}>
        <Text bold>
          {step === "project" ? "❯ " : "  "}Project{" "}
          {step !== "project" && project ? <Text color="cyan">{project.name}</Text> : null}
        </Text>
        {step === "project" && (
          <Box flexDirection="column">
            {projects.length === 0 && <Text dimColor>  (loading projects…)</Text>}
            {projects.slice(0, 8).map((p, i) => (
              <Text key={p.id}>
                <Text color={i === sel ? "cyan" : undefined}>{i === sel ? "  ▸ " : "    "}</Text>
                {p.name} <Text dimColor>[{p.status}]</Text>
              </Text>
            ))}
            <Text dimColor>  ↑/↓ select · Enter to continue</Text>
          </Box>
        )}
      </Box>

      {step !== "project" && (
        <Box flexDirection="column" marginTop={1}>
          <Text bold>{step === "goal" ? "❯ " : "  "}Goal</Text>
          <Box>
            <Text color="yellow">{"  › "}</Text>
            {step === "goal" ? (
              <TextInput
                value={goal}
                onChange={setGoal}
                onSubmit={() => void submit()}
                placeholder="what should the harness build? (Enter to launch)"
              />
            ) : (
              <Text>{goal}</Text>
            )}
          </Box>
        </Box>
      )}

      {step === "submitting" && (
        <Text color="yellow">
          {"  "}launching run…
        </Text>
      )}
      {err && <Text color="red">  error: {err}</Text>}
    </Box>
  );
}
