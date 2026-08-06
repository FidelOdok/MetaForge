import { useEffect, useRef, useState } from "react";
import { Box, Text, useApp, useInput } from "ink";
import { loadConfig, setConfigValue } from "./config.js";
import { GatewayClient } from "./api/client.js";
import { useChat } from "./hooks/useChat.js";
import { Chat } from "./components/Chat.js";
import { ContextMeter } from "./components/ContextMeter.js";
import { RunsView } from "./components/RunsView.js";
import { IntentForm } from "./components/IntentForm.js";
import { TwinView } from "./components/TwinView.js";
import { useRunAlerts } from "./hooks/useRunAlerts.js";
import { useTerminalSize } from "./hooks/useTerminalSize.js";
import {
  assistantScope,
  isDetachQuery,
  resolveProject,
  scopeLabel,
  type ChatScope,
} from "./lib/project.js";

type View = "chat" | "runs" | "new" | "twin";

const ALERT_COLOR = { gate: "yellow", done: "green", failed: "red" } as const;

/**
 * Root TUI: header + status bar + a switchable view. Ctrl+T = chat (streaming
 * assistant), Ctrl+R = runs (gated design-flow timeline + approvals).
 *
 * `initialProject` is `forge --project <id|name>`: it is resolved against the
 * gateway's project list before the chat thread is created, so the session
 * starts already scoped to that project.
 */
export function App({
  initialProject,
  continueLatest,
}: { initialProject?: string; continueLatest?: boolean } = {}) {
  const { exit } = useApp();
  const cfg = loadConfig();
  const [client] = useState(() => new GatewayClient(cfg));
  const [health, setHealth] = useState("checking…");
  const [view, setView] = useState<View>("chat");
  const [model, setModel] = useState<string | undefined>(cfg.model);
  const [provider, setProvider] = useState<string | undefined>(cfg.provider);
  // Requested chat scope. `null` while `--project` is being resolved, so useChat
  // holds off instead of creating a throwaway assistant thread first.
  const [scope, setScope] = useState<ChatScope | null>(() =>
    initialProject || continueLatest ? null : assistantScope(),
  );
  const [scopeError, setScopeError] = useState<string | null>(null);
  const { awaiting, alert } = useRunAlerts(client);
  const { rows } = useTerminalSize();

  // Own the chat thread here (not inside <Chat>) so it survives view switches —
  // ^R/^B/^N no longer tear down the SSE stream and drop the conversation.
  const chat = useChat(client, model, provider, scope);
  const chatRef = useRef(chat);
  chatRef.current = chat;

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
      } catch {
        if (alive) setHealth("unreachable");
      }
    })();
    return () => {
      alive = false;
    };
  }, [client]);

  // Resolve `--continue` once (MET-595): resume the most recent thread, or
  // fall back to a fresh assistant scope when there is nothing to resume.
  useEffect(() => {
    if (!continueLatest) return;
    let alive = true;
    void (async () => {
      try {
        const { pickerCandidates } = await import("./lib/resume.js");
        const threads = pickerCandidates(await client.listThreads(50), null, 1);
        if (!alive) return;
        if (threads.length) {
          chatRef.current?.resume(threads[0].id);
        } else {
          setScopeError("--continue: no resumable sessions — starting fresh");
          setScope(assistantScope());
        }
      } catch (e) {
        if (!alive) return;
        setScopeError(`--continue: ${(e as Error).message}`);
        setScope(assistantScope());
      }
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, continueLatest]);

  // Resolve `--project` once, then release the chat thread. A name that doesn't
  // resolve is reported and the session continues unscoped — the footer will say
  // "no project", so the failure is visible rather than assumed away.
  useEffect(() => {
    if (!initialProject) return;
    let alive = true;
    void (async () => {
      try {
        const r = resolveProject(await client.listProjects(), initialProject);
        if (!alive) return;
        if (r.ok) setScope(r.scope);
        else {
          setScopeError(`--project ${initialProject}: ${r.error}`);
          setScope(assistantScope());
        }
      } catch (e) {
        if (!alive) return;
        setScopeError(`--project ${initialProject}: ${(e as Error).message}`);
        setScope(assistantScope());
      }
    })();
    return () => {
      alive = false;
    };
  }, [client, initialProject]);

  /**
   * `/project [id|name|none]`. Returns the line to show the user; resolution
   * happens here (App owns the client and the scope) while <Chat> just renders
   * the result.
   */
  const changeProject = async (arg: string): Promise<string> => {
    const q = arg.trim();
    // Report the live thread's scope, but decide against the *requested* one —
    // during a switch the new thread may not exist yet, and asking twice for the
    // same project shouldn't recreate it.
    const live = chat.threadScope;
    if (!q) {
      return live?.kind === "project"
        ? `project: ${live.name} (${live.id})`
        : "project: none — /project <id|name> to scope this chat";
    }
    if (isDetachQuery(q)) {
      if (scope?.kind !== "project") return "project: none already";
      setScopeError(null);
      setScope(assistantScope());
      return "leaving the project — starting a new, unscoped thread";
    }
    const r = resolveProject(await client.listProjects(), q);
    if (!r.ok) return r.error;
    if (scope?.kind === "project" && scope.id === r.scope.id) return `already in ${r.scope.name}`;
    setScopeError(null);
    setScope(r.scope);
    return `project → ${r.scope.name} — starting a new thread in it`;
  };

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

      {/* A `--project` that didn't resolve. Stays put for the session: the chat
          is running unscoped and the user should know why. */}
      {scopeError && (
        <Box paddingX={1}>
          <Text color="red">✗ {scopeError}</Text>
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
            onProjectChange={changeProject}
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

      {/* Context-window gauge for the latest turn (chat only), just above the
          status footer. Only present once a harness turn has reported stats. */}
      {isChat && chat.contextStats ? <ContextMeter stats={chat.contextStats} /> : null}

      {/* Two stacked, truncating lines so a long project/model name never
          collides the way a single space-between row did. The project segment is
          the *live thread's* scope (useChat reports it after the gateway creates
          the thread) — never "whichever project the API listed first", which is
          what used to make an unscoped session look project-scoped. */}
      <Box flexDirection="column" paddingX={1}>
        <Text dimColor wrap="truncate">
          <Text color={healthColor}>● {health}</Text> · {gateway} ·{" "}
          {scopeLabel(chat.threadScope)} · {model ?? "default"} · {cfg.mode}
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
