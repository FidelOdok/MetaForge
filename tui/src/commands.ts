/**
 * Non-interactive command layer for the unified `forge` entrypoint.
 *
 * `forge <command> …` runs here (parse → HTTP → print → exit); bare `forge`
 * with a TTY launches the Ink TUI (see cli.tsx). Both share the same typed
 * gateway client, so there's one implementation, two modes.
 */
import { randomUUID } from "node:crypto";
import { GatewayClient, GatewayError } from "./api/client.js";
import { isTerminal, streamRunStatus } from "./api/runs.js";
import { BUILD } from "./build-info.js";
import { configPath, loadConfig, setConfigValue } from "./config.js";

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

const out = (x: unknown): void => {
  process.stdout.write(`${JSON.stringify(x, null, 2)}\n`);
};
const line = (s: string): void => {
  process.stdout.write(`${s}\n`);
};
function usage(msg: string): number {
  process.stderr.write(`${msg}\n`);
  return 2;
}

function printVersion(): void {
  const stamp = BUILD.date ? `${BUILD.commit}, ${BUILD.date}` : BUILD.commit;
  line(`forge ${BUILD.version} (${stamp})`);
}

function printHelp(): void {
  line(
    [
      "forge — MetaForge unified CLI + TUI",
      "",
      "Run bare in a terminal to launch the interactive TUI:",
      "  forge                 interactive TUI (chat · runs · new · twin)",
      "  forge ui              force the TUI",
      "",
      "Or use a command (scriptable; add --json for machine output):",
      "  forge runs list|get <id>|create|approve <id>|reject <id>|watch <id>",
      "  forge chat -m \"message\"        one-shot assistant turn",
      "  forge projects                list projects",
      "  forge twin list               list twin nodes",
      "  forge sources                 list ingested knowledge sources",
      '  forge memory retrieve "goal"  find similar past experiences',
      "  forge proposals list|approve <id>|reject <id>",
      "  forge config show|path|set <key> <value>",
      "  forge --version | --help",
      "",
      "Flags: --json  --gateway <url>  (chat: --model --provider)",
      "Config: ~/.forge/config.json (gateway_url, provider, model, mode)",
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
      const id = rest[0];
      if (!id) return usage(`forge runs ${sub} <id>`);
      const run = await client.submitApproval(id, sub === "approve" ? "approve" : "reject");
      if (json) out(run);
      else line(`${run.id} ${run.status}`);
      return 0;
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

  const thread = await client.createThread(`cli-${randomUUID().slice(0, 8)}`);
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
      const id = rest[0];
      if (!id) return usage(`forge proposals ${sub} <change_id>`);
      const reason = typeof flags.reason === "string" ? flags.reason : undefined;
      const r = await client.decideProposal(id, sub === "approve" ? "approve" : "reject", reason);
      if (json) out(r);
      else line(`${id} ${sub}d`);
      return 0;
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
      case "proposals":
        return await proposalsCmd(client, sub, rest, flags, json);
      case "config":
        return configCmd(sub, rest, json);
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
