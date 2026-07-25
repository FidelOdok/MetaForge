import { useEffect, useState } from "react";
import { Box, Text, useInput } from "ink";
import TextInput from "ink-text-input";
import type { GatewayClient, Project } from "../api/client.js";
import {
  INTENT_FIELDS,
  composeGoal,
  isComplete,
  missingFields,
  type IntentValues,
} from "../lib/intent.js";

type Phase = "project" | "fields" | "review" | "submitting";

/**
 * Elicits structured intent, then launches a gated design-flow run. Submission
 * is blocked until every required field is captured — the deterministic
 * completeness gate that keeps the goal from being fabricated downstream.
 */
export function IntentForm({
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
  const [pSel, setPSel] = useState(0);
  const [phase, setPhase] = useState<Phase>("project");
  const [idx, setIdx] = useState(0);
  const [values, setValues] = useState<IntentValues>({});
  const [draft, setDraft] = useState("");
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

  const project = projects[pSel];
  const field = INTENT_FIELDS[idx];
  const missing = missingFields(values);

  async function submit() {
    if (!project || !isComplete(values)) return;
    setPhase("submitting");
    setErr(null);
    try {
      const run = await client.createRun({
        goal: composeGoal(values),
        flow,
        project_id: project.id,
      });
      onCreated(run.id);
    } catch (e) {
      setErr((e as Error).message);
      setPhase("review");
    }
  }

  function commitField(value: string) {
    if (field) setValues((v) => ({ ...v, [field.key]: value.trim() }));
    setDraft("");
    if (idx < INTENT_FIELDS.length - 1) setIdx(idx + 1);
    else setPhase("review");
  }

  useInput((input, key) => {
    if (key.escape) {
      if (phase === "project") onCancel();
      else if (phase === "fields") {
        if (idx > 0) {
          setIdx(idx - 1);
          setDraft(values[INTENT_FIELDS[idx - 1]!.key] ?? "");
        } else setPhase("project");
      } else if (phase === "review") {
        setPhase("fields");
        setIdx(INTENT_FIELDS.length - 1);
      }
      return;
    }
    if (phase === "project") {
      if (key.upArrow) setPSel((s) => Math.max(0, s - 1));
      if (key.downArrow) setPSel((s) => Math.min(Math.max(0, projects.length - 1), s + 1));
      if (key.return && project) {
        setPhase("fields");
        setIdx(0);
        setDraft(values[INTENT_FIELDS[0]!.key] ?? "");
      }
    } else if (phase === "review") {
      if (key.return) void submit();
      if (input === "e") {
        setPhase("fields");
        setIdx(0);
        setDraft(values[INTENT_FIELDS[0]!.key] ?? "");
      }
    }
  });

  return (
    <Box flexDirection="column" paddingX={1}>
      <Text bold color="yellow">
        New run · intent capture <Text dimColor>· flow {flow} · Esc back</Text>
      </Text>

      <Box marginTop={1}>
        <Text dimColor>project </Text>
        <Text color="cyan">{project?.name ?? "—"}</Text>
        {phase !== "project" && (
          <Text dimColor>
            {"   "}captured {INTENT_FIELDS.length - missing.length}/{INTENT_FIELDS.length}
          </Text>
        )}
      </Box>

      {phase === "project" && (
        <Box flexDirection="column" marginTop={1}>
          {projects.slice(0, 8).map((p, i) => (
            <Text key={p.id}>
              <Text color={i === pSel ? "cyan" : undefined}>{i === pSel ? "  ▸ " : "    "}</Text>
              {p.name} <Text dimColor>[{p.status}]</Text>
            </Text>
          ))}
          <Text dimColor>  ↑/↓ select · Enter to begin</Text>
        </Box>
      )}

      {(phase === "fields" || phase === "review") && (
        <Box flexDirection="column" marginTop={1}>
          {INTENT_FIELDS.map((f, i) => {
            const val = values[f.key] ?? "";
            const active = phase === "fields" && i === idx;
            const empty = !val.trim();
            return (
              <Box key={f.key}>
                <Text>
                  <Text color={active ? "yellow" : empty ? "red" : "green"}>
                    {active ? "❯ " : empty ? "○ " : "● "}
                  </Text>
                  <Text dimColor>{f.label.padEnd(11)}</Text>
                </Text>
                {active ? (
                  <TextInput value={draft} onChange={setDraft} onSubmit={commitField} placeholder={f.prompt} />
                ) : (
                  <Text color={empty ? "red" : undefined}>{val || f.prompt}</Text>
                )}
              </Box>
            );
          })}
        </Box>
      )}

      {phase === "review" && (
        <Box marginTop={1} flexDirection="column">
          {missing.length > 0 ? (
            <Text color="red">
              ✗ incomplete — fill: {missing.join(", ")} <Text dimColor>(e to edit)</Text>
            </Text>
          ) : (
            <Text color="green">
              ✓ complete — <Text bold>Enter</Text> to launch the gated run{" "}
              <Text dimColor>(e to edit)</Text>
            </Text>
          )}
        </Box>
      )}

      {phase === "submitting" && <Text color="yellow">  launching run…</Text>}
      {err && <Text color="red">  error: {err}</Text>}
    </Box>
  );
}
