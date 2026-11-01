/**
 * Non-interactive command layer for the unified `forge` entrypoint.
 *
 * `forge <command> …` runs here (parse → HTTP → print → exit); bare `forge`
 * with a TTY launches the Ink TUI (see cli.tsx). Both share the same typed
 * gateway client, so there's one implementation, two modes.
 */
import { writeSync } from "node:fs";
import { createInterface } from "node:readline";
import {
  GatewayClient,
  GatewayError,
  type ApprovalDecision,
  type ApprovalItem,
} from "./api/client.js";
import { isTerminal, streamRunStatus } from "./api/runs.js";
import { loginChatGPT } from "./auth/oauth.js";
import { CLI_QUICKSTART, MISSION, plainBanner } from "./banner.js";
import { BUILD } from "./build-info.js";
import { configPath, loadConfig, setConfigValue } from "./config.js";
import { assistantScope, resolveProject, type ChatScope } from "./lib/project.js";

interface Parsed {
  _: string[];
  flags: Record<string, string | boolean>;
}

export function parseArgs(argv: string[]): Parsed {
  const _: string[] = [];
  const flags: Record<string, string | boolean> = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    let key: string | null = null;
    if (a.startsWith("--")) key = a.slice(2);
    else if (a.startsWith("-") && a.length > 1) key = a.slice(1);
    // `--flag=value` — the separate-word form below can't express a value that
    // starts with `-`, and it's the form people reach for with quoted names.
    if (key !== null && key.includes("=")) {
      const eq = key.indexOf("=");
      flags[key.slice(0, eq)] = key.slice(eq + 1);
      continue;
    }
    if (key !== null) {
      const next = argv[i + 1];
      if (next !== undefined && !next.startsWith("-")) {
        flags[key] = next;
        i++;
      } else {
        flags[key] = true;
      }
    } else {
      _.push(a);
    }
  }
  return { _, flags };
}

/**
 * FORGE-92: `process.stdout.write()` is asynchronous when stdout is a pipe,
 * and a bare `process.exit()` right after kills the process before buffered
 * output drains -- truncating any `--json` output larger than the pipe
 * buffer (observed: silently cut at exactly 128 KiB under Node, 64 KiB under
 * the Bun-compiled binary `forge` actually ships as; redirecting to a file
 * hid the bug because file writes are synchronous).
 *
 * A first pass tried waiting for the async write to drain before exiting
 * (an empty `write(callback)`, then a `'drain'` listener) -- both worked
 * under Node but NOT under Bun's compiled-binary stdout stream, which fires
 * either signal before the preceding large write has actually reached the
 * OS. Rather than chase Bun's async-stream timing further, this writes
 * synchronously via `fs.writeSync` on the raw fd, retrying on `EAGAIN` when
 * the pipe buffer is momentarily full -- verified correct under both
 * runtimes, including against a deliberately slow reader. Once every write
 * is synchronous there is nothing left to drain, so callers can `exit()`
 * immediately afterward.
 */
export function writeAllSync(fd: number, s: string): void {
  const buf = Buffer.from(s, "utf8");
  let offset = 0;
  while (offset < buf.length) {
    try {
      offset += writeSync(fd, buf, offset, buf.length - offset);
    } catch (err) {
      const code = (err as NodeJS.ErrnoException)?.code;
      if (code === "EAGAIN" || code === "EWOULDBLOCK") continue;
      throw err;
    }
  }
}

const out = (x: unknown): void => {
  writeAllSync(1, `${JSON.stringify(x, null, 2)}\n`);
};
const line = (s: string): void => {
  writeAllSync(1, `${s}\n`);
};
function usage(msg: string): number {
  writeAllSync(2, `${msg}\n`);
  return 2;
}

function printVersion(): void {
  const stamp = BUILD.date ? `${BUILD.commit}, ${BUILD.date}` : BUILD.commit;
  line(`forge ${BUILD.version} (${stamp})`);
}

function printHelp(): void {
  const quickstart = CLI_QUICKSTART.map(([cmd, desc]) => `  ${cmd.padEnd(36)}${desc}`);
  line(
    [
      plainBanner(),
      "",
      ...MISSION,
      "",
      "Getting started",
      ...quickstart,
      "",
      "Interactive workspace (bare `forge` in a terminal)",
      "  type to chat · Ctrl+R runs · Ctrl+B twin · Ctrl+N new run · Ctrl+T chat · Esc quit",
      "  forge --project <id|name>     start scoped to a project",
      "  /project <id|name>            switch project (new thread) · /project none to leave",
      "  /model <slug>                 switch model",
      "",
      "All commands (scriptable; add --json for machine output)",
      "  forge runs list|get <id>|create|approve <id>|reject <id>|watch <id>",
      "  forge approvals list [--project id] [--kind k] [--all|--decided]   pending decisions",
      "  forge approvals show <id>     full card: findings, allowed decisions, rework targets",
      "  forge approvals approve <id> [--reason t] | reject|retry <id> --reason t",
      "  forge approvals rework <id> --to <phase> --reason t",
      '  forge chat -m "message" [--project <id|name>]   one-shot assistant turn',
      "  forge projects                list projects",
      "  forge twin list               list twin nodes",
      "  forge sources                 list ingested knowledge sources",
      '  forge memory retrieve "goal"  find similar past experiences',
      "  forge proposals list|approve <id>|reject <id>",
      "  forge auth list|login|use <provider>|logout <provider>",
      "  forge config show|path|set <key> <value>",
      "  forge --version | --help",
      "",
      "Flags: --json  --gateway <url>  --debug  (chat: --model --provider --project)",
      "Config: ~/.forge/config.json (gateway_url, provider, model, mode)",
      "Logs:   ~/.forge/logs/session.log  (--debug or FORGE_LOG=1 adds raw SSE)",
    ].join("\n"),
  );
}

async function runsCmd(
  client: GatewayClient,
  sub: string | undefined,
  rest: string[],
  flags: Record<string, string | boolean>,
  json: boolean,
): Promise<number> {
  switch (sub) {
    case undefined:
    case "list": {
      const runs = await client.listRuns();
      if (json) out(runs);
      else if (!runs.length) line("(no runs)");
      else for (const r of runs) line(`${r.id.padEnd(22)} ${r.status}`);
      return 0;
    }
    case "get": {
      const id = rest[0];
      if (!id) return usage("forge runs get <id>");
      out(await client.getRun(id));
      return 0;
    }
    case "create": {
      let request: Record<string, unknown>;
      if (typeof flags["request-json"] === "string") {
        request = JSON.parse(flags["request-json"]) as Record<string, unknown>;
      } else if (typeof flags.goal === "string") {
        request = { goal: flags.goal };
      } else {
        return usage('forge runs create --goal "text" | --request-json \'{...}\'');
      }
      const run = await client.createRun(request, flags["no-start"] !== true);
      if (json) out(run);
      else line(`${run.id} ${run.status}`);
      return 0;
    }
    case "approve":
    case "reject": {
      // Alias for `forge approvals approve|reject gate:<run_id>` (FORGE-509).
      const id = rest[0];
      if (!id) return usage(`forge runs ${sub} <id> [--reason text]`);
      return decideCmd(client, id.includes(":") ? id : `gate:${id}`, sub, flags, json);
    }
    case "watch": {
      const id = rest[0];
      if (!id) return usage("forge runs watch <id>");
      const controller = new AbortController();
      for await (const ev of streamRunStatus(client.baseUrl(), id, controller.signal)) {
        if (json) line(JSON.stringify(ev));
        else {
          const reason = ev.approval_reason ? ` — ${ev.approval_reason.slice(0, 80)}` : "";
          line(`${ev.status}${reason}`);
        }
        if (isTerminal(ev.status)) break;
      }
      return 0;
    }
    default:
      return usage(`unknown: forge runs ${sub}`);
  }
}

/** Exit codes for `forge approvals` (documented in docs/cli-reference.md). */
export const EXIT_ERROR = 1;
export const EXIT_USAGE = 2;
export const EXIT_NOT_FOUND = 3;
export const EXIT_CONFLICT = 4;
export const EXIT_INVALID = 5;
export const EXIT_AUTH = 6;

/** Map a gateway status to an approvals exit code. */
export function approvalExitCode(status: number | undefined): number {
  switch (status) {
    case 404:
      return EXIT_NOT_FOUND;
    case 409:
      return EXIT_CONFLICT;
    case 422:
      return EXIT_INVALID;
    case 401:
    case 403:
      return EXIT_AUTH;
    default:
      return EXIT_ERROR;
  }
}

/** Local pre-flight against the item's own policy; returns an error or null. */
export function validateDecision(
  item: ApprovalItem,
  decision: ApprovalDecision,
  reason: string | undefined,
  toPhase: string | undefined,
): string | null {
  const allowed = item.allowed_decisions ?? [];
  if (!allowed.includes(decision)) {
    return `${decision} is not allowed for ${item.id} (allowed: ${allowed.join(", ") || "none"})`;
  }
  if ((item.reason_required_for ?? []).includes(decision) && !(reason ?? "").trim()) {
    return `${decision} requires --reason`;
  }
  if (decision === "rework") {
    const targets = item.rework_targets ?? [];
    if (!toPhase) return `rework requires --to <phase> (one of: ${targets.join(", ") || "none"})`;
    if (!targets.includes(toPhase)) {
      return `--to ${toPhase} is not a rework target (one of: ${targets.join(", ") || "none"})`;
    }
  }
  return null;
}

/** Human-readable card with every ApprovalItem field. */
export function formatApproval(item: ApprovalItem): string {
  const lines = [
    item.title,
    `  id:           ${item.id}`,
    `  kind:         ${item.kind}`,
    `  status:       ${item.status}`,
    `  project:      ${item.project_id ?? "-"}`,
    `  created:      ${item.created_at}`,
    `  deadline:     ${item.deadline ?? "-"}`,
    `  route:        ${item.route ?? "-"}`,
    `  requested by: ${item.requested_by ?? "-"}`,
  ];
  if (item.summary) lines.push(`  summary:      ${item.summary}`);
  if (item.reason_held) lines.push(`  held because: ${item.reason_held}`);
  const findings = item.findings ?? [];
  lines.push(`  findings:     ${findings.length || "none"}`);
  for (const f of findings) lines.push(`    [${f.severity}] ${f.kind}: ${f.message}`);
  lines.push(`  allowed:      ${(item.allowed_decisions ?? []).join(", ") || "none"}`);
  lines.push(`  reason needed for: ${(item.reason_required_for ?? []).join(", ") || "-"}`);
  lines.push(`  rework targets:    ${(item.rework_targets ?? []).join(", ") || "-"}`);
  lines.push(
    `  decidable:    ${item.decidable ? "yes" : `no (${item.not_decidable_reason ?? "?"})`}`,
  );
  const detail = Object.entries(item.detail ?? {});
  if (detail.length) {
    lines.push("  detail:");
    for (const [k, v] of detail) lines.push(`    ${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`);
  }
  if (item.decision) {
    lines.push("  decision:");
    for (const [k, v] of Object.entries(item.decision)) lines.push(`    ${k}: ${String(v)}`);
  }
  return lines.join("\n");
}

function fail(code: number, message: string, json: boolean): number {
  if (json) out({ error: message, exit_code: code });
  else writeAllSync(2, `error: ${message}\n`);
  return code;
}

/** Fetch, validate locally, post a decision, print. Returns an exit code. */
async function decideCmd(
  client: GatewayClient,
  id: string,
  decision: ApprovalDecision,
  flags: Record<string, string | boolean>,
  json: boolean,
): Promise<number> {
  const reason = typeof flags.reason === "string" ? flags.reason : undefined;
  const toPhase = typeof flags.to === "string" ? flags.to : undefined;
  try {
    const item = await client.getApproval(id);
    if (item.decidable === false) {
      return fail(EXIT_CONFLICT, `not decidable right now: ${item.not_decidable_reason ?? "unknown"}`, json);
    }
    const problem = validateDecision(item, decision, reason, toPhase);
    if (problem) return fail(EXIT_INVALID, problem, json);
    const updated = await client.decideApproval(
      id,
      decision,
      reason,
      decision === "rework" ? toPhase : undefined,
    );
    if (json) out(updated);
    else line(`${updated.id ?? id} -> ${updated.status}`);
    return 0;
  } catch (e) {
    if (!(e instanceof GatewayError)) throw e;
    const code = approvalExitCode(e.status);
    const msg =
      code === EXIT_NOT_FOUND
        ? `no approval with id ${id}`
        : code === EXIT_CONFLICT
          ? `not decidable right now: ${e.message}`
          : code === EXIT_AUTH
            ? `not authorized (${e.message}); set METAFORGE_AUTH_TOKEN`
            : e.message;
    return fail(code, msg, json);
  }
}

async function approvalsCmd(
  client: GatewayClient,
  sub: string | undefined,
  rest: string[],
  flags: Record<string, string | boolean>,
  json: boolean,
): Promise<number> {
  switch (sub) {
    case undefined:
    case "list": {
      const status = flags.all === true ? "all" : flags.decided === true ? "decided" : "pending";
      try {
        const res = await client.listApprovals({
          status,
          project: typeof flags.project === "string" ? flags.project : undefined,
          kind: typeof flags.kind === "string" ? flags.kind : undefined,
        });
        if (json) out(res);
        else {
          if (!res.items.length) line("(no approvals)");
          for (const i of res.items) {
            line(`${i.id.padEnd(34)} ${i.kind.padEnd(14)} ${i.status.padEnd(9)} ${i.title}`);
          }
          if (res.unscoped_count) {
            line(`(${res.unscoped_count} approval(s) have no project and are not matched by --project)`);
          }
        }
        return 0;
      } catch (e) {
        if (!(e instanceof GatewayError)) throw e;
        return fail(approvalExitCode(e.status), e.message, json);
      }
    }
    case "show": {
      const id = rest[0];
      if (!id) return usage("forge approvals show <id> [--json]");
      try {
        const item = await client.getApproval(id);
        if (json) out(item);
        else line(formatApproval(item));
        return 0;
      } catch (e) {
        if (!(e instanceof GatewayError)) throw e;
        const code = approvalExitCode(e.status);
        return fail(code, code === EXIT_NOT_FOUND ? `no approval with id ${id}` : e.message, json);
      }
    }
    case "approve":
    case "reject":
    case "retry":
    case "rework": {
      const id = rest[0];
      if (!id) {
        return usage(
          sub === "rework"
            ? "forge approvals rework <id> --to <phase> --reason text"
            : `forge approvals ${sub} <id>${sub === "approve" ? " [--reason text]" : " --reason text"}`,
        );
      }
      return decideCmd(client, id, sub, flags, json);
    }
    default:
      return usage(`unknown: forge approvals ${sub}`);
  }
}

async function chatCmd(
  client: GatewayClient,
  flags: Record<string, string | boolean>,
  json: boolean,
): Promise<number> {
  const message = flags.message ?? flags.m;
  if (typeof message !== "string" || !message.trim()) {
    return usage('forge chat -m "message"  (one-shot; run `forge` bare for interactive chat)');
  }
  const cfg = loadConfig();
  const model = typeof flags.model === "string" ? flags.model : cfg.model;
  const provider = typeof flags.provider === "string" ? flags.provider : cfg.provider;

  // `--project <id|name>` scopes the one-shot the same way it scopes the
  // workspace: the gateway leads the turn with the project brief, so the answer
  // is grounded in that project's work products.
  let scope: ChatScope = assistantScope("cli");
  if (typeof flags.project === "string") {
    const r = resolveProject(await client.listProjects(), flags.project);
    if (!r.ok) return usage(`--project ${flags.project}: ${r.error}`);
    scope = r.scope;
  }

  const thread = await client.createThread(scope);
  await client.sendMessage(thread.id, message, { model, provider });
  const t = await client.getThread(thread.id);
  const msgs = t.messages ?? [];
  const last = msgs[msgs.length - 1];
  const replyText = (last?.content ?? last?.text ?? "").trim();
  const reply = replyText && replyText !== message ? replyText : "(no reply)";
  if (json) out({ thread_id: thread.id, reply });
  else line(reply);
  return 0;
}

async function twinCmd(client: GatewayClient, json: boolean): Promise<number> {
  const nodes = await client.listTwinNodes();
  if (json) out(nodes);
  else for (const n of nodes.slice(0, 50)) line(`${n.id}  ${n.type.padEnd(14)} ${n.name}`);
  return 0;
}

async function memoryCmd(
  client: GatewayClient,
  sub: string | undefined,
  rest: string[],
  flags: Record<string, string | boolean>,
  json: boolean,
): Promise<number> {
  const goal = sub === "retrieve" ? rest[0] : sub;
  if (!goal) return usage('forge memory retrieve "goal text"');
  const limit = typeof flags.limit === "string" ? Number(flags.limit) : 5;
  const hits = await client.memoryRetrieve(goal, limit);
  if (json) out(hits);
  else if (!hits.length) line("(no similar experiences)");
  else
    for (const h of hits) {
      line(`${Number(h.similarity ?? 0).toFixed(3)}  ${h.agentCode ?? "?"}  ${h.resultSummary ?? ""}`);
    }
  return 0;
}

async function proposalsCmd(
  client: GatewayClient,
  sub: string | undefined,
  rest: string[],
  flags: Record<string, string | boolean>,
  json: boolean,
): Promise<number> {
  switch (sub) {
    case undefined:
    case "list": {
      const ps = await client.listProposals();
      if (json) out(ps);
      else if (!ps.length) line("(no pending proposals)");
      else
        for (const p of ps) {
          const id = String(p.change_id ?? p.id ?? "").slice(0, 12);
          line(`${id}  ${p.status ?? ""}  ${p.description ?? p.title ?? ""}`);
        }
      return 0;
    }
    case "approve":
    case "reject": {
      // Alias for `forge approvals approve|reject change:<id>` (FORGE-509).
      const id = rest[0];
      if (!id) return usage(`forge proposals ${sub} <change_id> [--reason text]`);
      return decideCmd(client, id.includes(":") ? id : `change:${id}`, sub, flags, json);
    }
    default:
      return usage(`unknown: forge proposals ${sub}`);
  }
}

function configCmd(sub: string | undefined, rest: string[], json: boolean): number {
  switch (sub) {
    case undefined:
    case "show":
      out(loadConfig());
      return 0;
    case "path":
      line(configPath());
      return 0;
    case "set": {
      const [key, value] = rest;
      if (!key || value === undefined) return usage("forge config set <key> <value>");
      const cfg = setConfigValue(key, value);
      if (json) out(cfg);
      else line(`${key} = ${value}`);
      return 0;
    }
    default:
      return usage(`unknown: forge config ${sub}`);
  }
}

/**
 * Which `forge auth login` method to use for a provider: an explicit
 * `--method` flag always wins; otherwise codex-family providers (checked by
 * BOTH `family` and `providerId`, since the registry's CODEX constant is
 * literally the string "openai-codex", not "codex") default to OAuth, and
 * everything else defaults to an API key.
 */
export function resolveLoginMethod(
  explicit: string | undefined,
  family: string | undefined,
  providerId: string,
): "oauth" | "api-key" {
  if (explicit === "oauth" || explicit === "api-key") return explicit;
  const isCodex = family === "openai-codex" || providerId === "openai-codex";
  return isCodex ? "oauth" : "api-key";
}

function ask(q: string): Promise<string> {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => rl.question(q, (a) => (rl.close(), resolve(a.trim()))));
}

/** Prompt without echoing (for API keys). Falls back to a visible prompt if the
 * runtime doesn't support muting the readline output. */
function askHidden(q: string): Promise<string> {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  process.stdout.write(q);
  (rl as unknown as { _writeToOutput: (s: string) => void })._writeToOutput = () => {};
  return new Promise((resolve) =>
    rl.question("", (a) => (rl.close(), process.stdout.write("\n"), resolve(a.trim()))),
  );
}

async function authCmd(
  client: GatewayClient,
  sub: string | undefined,
  rest: string[],
  flags: Record<string, string | boolean>,
  json: boolean,
): Promise<number> {
  switch (sub) {
    case undefined:
    case "list": {
      const p = await client.listHarnessProviders();
      const active = (p.active_provider ?? "").toLowerCase();
      const rows = p.providers.map((pr) => ({
        provider: pr.id,
        family: pr.family,
        configured: pr.configured,
        active: pr.id.toLowerCase() === active,
      }));
      if (json) {
        out({ active_provider: p.active_provider, active_model: p.active_model, providers: rows });
      } else {
        line(`active: ${p.active_provider ?? "(none)"}${p.active_model ? ` · ${p.active_model}` : ""}`);
        for (const r of rows) {
          line(`  [${r.configured ? "✓" : " "}] ${r.provider.padEnd(20)} ${r.family}${r.active ? "   (active)" : ""}`);
        }
      }
      return 0;
    }
    case "use": {
      const provider = rest[0]?.toLowerCase();
      if (!provider) return usage("forge auth use <provider> [--model M]");
      const model = typeof flags.model === "string" ? flags.model : undefined;
      await client.setSelection(provider, model);
      setConfigValue("provider", provider);
      if (model) setConfigValue("model", model);
      line(`✓ active provider → ${provider}${model ? ` · ${model}` : ""}`);
      return 0;
    }
    case "logout": {
      const provider = rest[0]?.toLowerCase();
      if (!provider) return usage("forge auth logout <provider>");
      await client.deleteCredential(provider);
      line(`✓ forgot the stored credential for ${provider}`);
      return 0;
    }
    case "login": {
      const resp = await client.listHarnessProviders();
      let provider = typeof flags.provider === "string" ? flags.provider.toLowerCase() : undefined;
      if (!provider) {
        line("Providers (✓ = configured on the gateway):");
        resp.providers.forEach((pr, i) =>
          line(`  ${String(i + 1).padStart(2)}. [${pr.configured ? "✓" : " "}] ${pr.id} (${pr.family})`),
        );
        const pick = await ask("Choose a provider (number or id): ");
        const n = Number(pick);
        provider =
          Number.isInteger(n) && n >= 1 && n <= resp.providers.length
            ? resp.providers[n - 1]!.id
            : pick.toLowerCase();
      }
      const family = resp.providers.find((p) => p.id.toLowerCase() === provider)?.family;
      const method = resolveLoginMethod(
        typeof flags.method === "string" ? flags.method : undefined,
        family,
        provider!,
      );

      if (method === "oauth") {
        line(`Starting OAuth login for ${provider} — a browser will open (localhost:1455)…`);
        const tokens = await loginChatGPT({
          open: flags["no-browser"] !== true,
          onUrl: (u) => line(`If your browser didn't open, visit:\n  ${u}`),
        });
        await client.setCredential({
          provider: provider!,
          method: "oauth",
          tokens: {
            access_token: tokens.access_token,
            refresh_token: tokens.refresh_token,
            id_token: tokens.id_token,
          },
        });
        line(`✓ logged in to ${provider} (OAuth) — credential stored on the gateway.`);
      } else {
        const key =
          typeof flags["api-key"] === "string"
            ? flags["api-key"]
            : await askHidden(`API key for ${provider}: `);
        if (!key) return usage("no API key provided");
        const baseUrl = typeof flags["base-url"] === "string" ? flags["base-url"] : undefined;
        await client.setCredential({
          provider: provider!,
          method: "api_key",
          api_key: key,
          base_url: baseUrl,
        });
        line(`✓ stored an API key for ${provider} on the gateway.`);
      }

      if (flags["no-activate"] !== true) {
        const model = typeof flags.model === "string" ? flags.model : undefined;
        await client.setSelection(provider!, model);
        setConfigValue("provider", provider!);
        if (model) setConfigValue("model", model);
        line(`✓ active provider → ${provider}${model ? ` · ${model}` : ""}`);
      }
      return 0;
    }
    default:
      return usage(`unknown: forge auth ${sub}`);
  }
}

/** Dispatch a non-interactive command. Returns a process exit code. */
export async function runCommand(argv: string[]): Promise<number> {
  const { _, flags } = parseArgs(argv);
  const [cmd, sub, ...rest] = _;
  const json = flags.json === true;

  if (cmd === "help" || flags.help === true || flags.h === true) {
    printHelp();
    return 0;
  }
  if (cmd === "version" || flags.version === true || flags.v === true) {
    printVersion();
    return 0;
  }

  const cfg = loadConfig();
  if (typeof flags.gateway === "string") cfg.gateway_url = flags.gateway;
  const client = new GatewayClient(cfg);

  try {
    switch (cmd) {
      case "runs":
        return await runsCmd(client, sub, rest, flags, json);
      case "chat":
        return await chatCmd(client, flags, json);
      case "projects":
      case "project": {
        const projects = await client.listProjects();
        if (json) out(projects);
        else for (const p of projects) line(`${p.name}  [${p.status}]  ${p.work_products?.length ?? 0} wp`);
        return 0;
      }
      case "twin":
        return await twinCmd(client, json);
      case "sources": {
        const sources = await client.listSources();
        if (json) out(sources);
        else for (const s of sources) line(`${s.knowledgeType ?? "?"}  ${s.sourcePath ?? ""}  (${s.fragmentCount ?? 0})`);
        return 0;
      }
      case "memory":
        return await memoryCmd(client, sub, rest, flags, json);
      case "approvals":
        return await approvalsCmd(client, sub, rest, flags, json);
      case "proposals":
        return await proposalsCmd(client, sub, rest, flags, json);
      case "config":
        return configCmd(sub, rest, json);
      case "auth":
        return await authCmd(client, sub, rest, flags, json);
      default:
        process.stderr.write(`unknown command: ${cmd ?? "(none)"}\n\n`);
        printHelp();
        return 2;
    }
  } catch (e) {
    const err = e as Error;
    if (json) out({ error: err.message });
    else {
      const prefix = e instanceof GatewayError ? "gateway error" : "error";
      process.stderr.write(`${prefix}: ${err.message}\n`);
    }
    return 1;
  }
}
