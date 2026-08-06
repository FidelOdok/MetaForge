import { useRef, useState } from "react";
import { Box, Static, Text, useInput } from "ink";
import TextInput from "ink-text-input";
import type { GatewayClient } from "../api/client.js";
import type { ChatMessage, UseChat } from "../hooks/useChat.js";
import { useTerminalSize } from "../hooks/useTerminalSize.js";
import type { ThreadSummary } from "../api/client.js";
import { describeThread, pickerCandidates } from "../lib/resume.js";
import { appendHistory, loadHistory } from "../history.js";
import { StepTrace } from "./StepTrace.js";
import { Thinking } from "./Thinking.js";
import { Welcome, welcomeHeight } from "./Welcome.js";

/** A completed conversation turn, rendered once into <Static> and never again. */
function Turn({ m }: { m: ChatMessage }) {
  // A local notice (project switch, degraded scope) — not part of the
  // conversation the agent sees, so it renders dim and unattributed.
  if (m.role === "system") {
    return (
      <Box paddingX={1} marginTop={1}>
        <Text dimColor>{m.text}</Text>
      </Box>
    );
  }
  return (
    <Box flexDirection="column" paddingX={1} marginTop={1}>
      {m.role === "user" ? (
        <>
          <Text color="blueBright" bold>
            ❯ you
          </Text>
          <Text>{m.text}</Text>
        </>
      ) : (
        <>
          {m.steps && m.steps.length ? (
            <Box flexDirection="column">
              <Text dimColor>· thinking</Text>
              <Box marginLeft={1}>
                <StepTrace steps={m.steps} />
              </Box>
            </Box>
          ) : null}
          <Text color="magenta" bold>
            ◆ assistant
          </Text>
          {m.text ? (
            <Text>{m.text}</Text>
          ) : (
            <Text dimColor>(no reply — {m.reason ?? "the agent didn't answer"})</Text>
          )}
        </>
      )}
    </Box>
  );
}

/** The chat view: a scrollback of finalized turns (top → down) plus a live,
 * bottom-pinned region for the streaming answer and the input box. */
export function Chat({
  client,
  model,
  provider,
  onModelChange,
  onProviderChange,
  onProjectChange,
  chat,
}: {
  client: GatewayClient;
  model?: string;
  provider?: string;
  onModelChange?: (model: string) => void;
  onProviderChange?: (provider: string) => void;
  /** `/project [id|name|none]` — resolves in App; resolves to the notice to show. */
  onProjectChange?: (arg: string) => Promise<string>;
  /** Chat thread state, owned by App so it survives view switches and so App's
   *  height policy and this component's layout branch flip in the SAME render
   *  (a callback would lag one render behind and strand transition frames). */
  chat: UseChat;
}) {
  const { status, error, messages, pending, send } = chat;
  const [input, setInput] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  // MET-595: /resume thread picker — when non-null it replaces the input box.
  const [picker, setPicker] = useState<ThreadSummary[] | null>(null);
  const [pickerIdx, setPickerIdx] = useState(0);
  const busy = status === "thinking";
  const reconnecting = status === "reconnecting";
  const { rows: termRows, cols } = useTerminalSize();

  // On a fresh session (no turns yet) we still want the input pinned to the
  // bottom of the terminal — a proper "app" launch screen — but the Welcome
  // splash must live in <Static> from the very first frame (see below), never
  // moved between the dynamic frame and Static. `started` selects the launch
  // spacer vs. the plain content-flow transcript.
  const started = messages.length > 0;

  // Shell-style prompt history: ↑/↓ recall previous inputs, persisted across
  // sessions. `histPos` = index into history while browsing (null = live draft,
  // which we stash so ↓ past the newest restores what was being typed). The
  // transcript itself scrolls with the terminal's own scrollback now that
  // finalized turns render via <Static>, so there is no in-app scroll to manage.
  const [history, setHistory] = useState<string[]>(() => loadHistory());
  const histPos = useRef<number | null>(null);
  const draft = useRef("");

  useInput((_i, key) => {
    // Picker mode captures navigation keys (MET-595).
    if (picker !== null) {
      if (key.upArrow) setPickerIdx((i) => Math.max(0, i - 1));
      else if (key.downArrow) setPickerIdx((i) => Math.min(picker.length - 1, i + 1));
      else if (key.return) {
        const chosen = picker[pickerIdx];
        setPicker(null);
        if (chosen) {
          chat.resume(chosen.id);
          setNotice(`resuming "${chosen.title || chosen.id.slice(0, 8)}" …`);
        }
      } else if (key.escape) {
        setPicker(null);
        setNotice(null);
      }
      return;
    }
    if (history.length === 0) return;
    if (key.upArrow) {
      if (histPos.current === null) {
        draft.current = input;
        histPos.current = history.length - 1;
      } else {
        histPos.current = Math.max(0, histPos.current - 1);
      }
      setInput(history[histPos.current]);
    } else if (key.downArrow && histPos.current !== null) {
      if (histPos.current >= history.length - 1) {
        histPos.current = null;
        setInput(draft.current);
      } else {
        histPos.current += 1;
        setInput(history[histPos.current]);
      }
    }
  });

  // Typing exits history-browsing so edits start a fresh draft.
  const onInputChange = (value: string) => {
    histPos.current = null;
    setInput(value);
  };

  /** Handle in-app slash commands; returns true if the input was a command. */
  const handleSlash = (value: string): boolean => {
    if (!value.startsWith("/")) return false;
    const [cmd, ...args] = value.slice(1).trim().split(/\s+/);
    const arg = args.join(" ");
    switch (cmd) {
      case "model":
        if (arg) {
          onModelChange?.(arg);
          setNotice(`model → ${arg}`);
        } else {
          setNotice(`model: ${model ?? "default"}`);
        }
        return true;
      case "provider":
        if (arg) {
          onProviderChange?.(arg);
          setNotice(`provider → ${arg}`);
        } else {
          setNotice(`provider: ${provider ?? "default"}`);
        }
        return true;
      case "project":
        if (!onProjectChange) {
          setNotice("/project is unavailable here");
          return true;
        }
        // Resolution needs the gateway (name → id), so the notice lands async.
        if (arg) setNotice(`resolving project "${arg}"…`);
        void onProjectChange(arg).then(setNotice, (e: Error) =>
          setNotice(`/project failed: ${e.message}`),
        );
        return true;
      case "resume":
        setNotice("loading sessions…");
        void client
          .listThreads(50)
          .then((threads) => {
            const items = pickerCandidates(threads, chat.threadId);
            if (!items.length) {
              setNotice("no resumable sessions found");
              return;
            }
            setPickerIdx(0);
            setPicker(items);
            setNotice(null);
          })
          .catch((e: Error) => setNotice(`/resume failed: ${e.message}`));
        return true;
      case "help":
        setNotice(
          "/resume · /project <id|name> · /model <slug> · /provider <id> · /help · Esc quit",
        );
        return true;
      default:
        setNotice(`unknown command: /${cmd} (try /help)`);
        return true;
    }
  };

  const onSubmit = (value: string) => {
    if (!value.trim() || busy) return;
    setNotice(null);
    // Record every submitted input (messages and slash commands), skipping an
    // immediate duplicate; reset the history cursor.
    setHistory((h) => (h[h.length - 1] === value ? h : [...h, value]));
    appendHistory(value);
    histPos.current = null;
    draft.current = "";
    if (handleSlash(value)) {
      setInput("");
      return;
    }
    send(value);
    setInput("");
  };

  const placeholder =
    status === "connecting"
      ? "connecting…"
      : reconnecting
        ? "reconnecting…"
        : "message  (/model <slug> · Esc quit)";

  // <Static> content: the Welcome splash first (so it sits at the very top of
  // the session) followed by each finalized turn. Ink commits these to the
  // terminal exactly once and never repaints them, so a keystroke can only ever
  // re-render the live input box below — the transcript can't flicker. Crucially
  // Welcome is a Static row from the FIRST frame, empty session included: moving
  // it between the dynamic frame and Static is what stranded a whole duplicate
  // splash + input into the scrollback on the first turn.
  type Row = { key: string; welcome?: true; m?: ChatMessage };
  const rows: Row[] = [
    { key: "welcome", welcome: true },
    ...messages.map((m, i) => ({ key: `m${i}`, m })),
  ];
  const staticContent = (
    <Static items={rows}>
      {(row) =>
        row.welcome ? (
          <Welcome key={row.key} gatewayUrl={client.baseUrl()} />
        ) : (
          <Turn key={row.key} m={row.m as ChatMessage} />
        )
      }
    </Static>
  );

  // The live region — the only part that repaints during a turn: the streaming
  // in-flight answer, transient banners, and the input box. Shared by both the
  // launch layout and the Static transcript layout.
  const liveRegion = (
    <>
      {/* Live in-flight turn: streams here, then moves into <Static> on
          completion. */}
      {pending ? (
        <Box flexDirection="column" paddingX={1} marginTop={1}>
          {pending.steps.length || pending.thinking || pending.startedAction ? (
            <Box flexDirection="column">
              <Text dimColor>
                · thinking
                {pending.thinking || pending.text
                  ? ` · ~${Math.max(1, Math.round(((pending.thinking?.length ?? 0) + pending.text.length) / 4))} tok`
                  : ""}
              </Text>
              <Box marginLeft={1} flexDirection="column">
                {pending.steps.length ? <StepTrace steps={pending.steps} /> : null}
                {pending.thinking ? (
                  <Text dimColor wrap="truncate-end">
                    {pending.thinking.slice(-600)}
                  </Text>
                ) : null}
                {pending.startedAction ? (
                  <Text color="yellow">→ calling {pending.startedAction} …</Text>
                ) : null}
              </Box>
            </Box>
          ) : null}
          {pending.text ? (
            <>
              <Text color="magenta" bold>
                ◆ assistant
              </Text>
              <Text>
                {pending.text}
                {busy ? <Text color="yellow">▌</Text> : null}
              </Text>
            </>
          ) : busy ? (
            <Thinking />
          ) : null}
        </Box>
      ) : null}

      {reconnecting ? (
        <Box paddingX={1}>
          <Thinking label="reconnecting to gateway" />
        </Box>
      ) : null}
      {error && !reconnecting ? (
        <Box paddingX={1}>
          <Text color="red">error: {error}</Text>
        </Box>
      ) : null}
      {notice ? (
        <Box paddingX={1}>
          <Text color="cyan">{notice}</Text>
        </Box>
      ) : null}

      {/* MET-595: /resume picker replaces the input box while open. */}
      {picker !== null ? (
        <Box marginX={1} borderStyle="round" borderColor="cyan" paddingX={1} flexDirection="column">
          <Text color="cyan" bold>
            resume a session  (↑/↓ · Enter · Esc)
          </Text>
          {picker.map((t, i) => (
            <Text key={t.id} color={i === pickerIdx ? "cyan" : undefined} inverse={i === pickerIdx}>
              {describeThread(t)}
            </Text>
          ))}
        </Box>
      ) : (
        /* Input pinned to the bottom of the live region. */
        <Box
          marginX={1}
          borderStyle="round"
          borderColor={busy || reconnecting ? "yellow" : "blue"}
          paddingX={1}
        >
          <Text color={busy || reconnecting ? "yellow" : "blue"}>{busy ? "… " : "› "}</Text>
          <TextInput
            value={input}
            onChange={onInputChange}
            onSubmit={onSubmit}
            placeholder={placeholder}
          />
        </Box>
      )}
    </>
  );

  // Launch layout: no turns yet. Welcome is already committed to Static (above);
  // below it we reserve a fixed-height box for exactly the rows between the
  // splash and the status footer, and justify its content to the bottom so the
  // input pins to the bottom of the terminal. Because Welcome never leaves
  // Static, the first turn just appends to the scrollback and drops this spacer
  // box — no frame is stranded. `FOOTER_ROWS` is App's two-line status bar; the
  // extra row keeps the splash fully on-screen if the height estimate is off.
  if (!started) {
    const FOOTER_ROWS = 2;
    const spacerHeight = Math.max(
      3,
      termRows - welcomeHeight(cols, client.baseUrl()) - FOOTER_ROWS - 1,
    );
    return (
      <Box flexDirection="column" flexGrow={1}>
        {staticContent}
        <Box flexDirection="column" height={spacerHeight} justifyContent="flex-end">
          {liveRegion}
        </Box>
      </Box>
    );
  }

  // Transcript layout: finalized turns live in <Static> (native scrollback,
  // never repainted), the live region follows the content below them.
  return (
    <Box flexDirection="column" flexGrow={1}>
      {staticContent}
      {liveRegion}
    </Box>
  );
}
