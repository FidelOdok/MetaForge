import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs, resolveLoginMethod } from "./commands.js";

test("splits positionals from long and short flags", () => {
  const p = parseArgs(["runs", "get", "run_123", "--json"]);
  assert.deepEqual(p._, ["runs", "get", "run_123"]);
  assert.equal(p.flags.json, true);
});

test("long flag with a value", () => {
  const p = parseArgs(["runs", "create", "--goal", "build a bracket"]);
  assert.deepEqual(p._, ["runs", "create"]);
  assert.equal(p.flags.goal, "build a bracket");
});

test("short flag with a value (-m)", () => {
  const p = parseArgs(["chat", "-m", "hello there"]);
  assert.deepEqual(p._, ["chat"]);
  assert.equal(p.flags.m, "hello there");
});

test("boolean flag when no value follows", () => {
  const p = parseArgs(["projects", "--json"]);
  assert.equal(p.flags.json, true);
});

test("--flag=value form", () => {
  const p = parseArgs(["chat", "--project=Monitor Build Demo", "--json"]);
  assert.deepEqual(p._, ["chat"]);
  assert.equal(p.flags.project, "Monitor Build Demo");
  assert.equal(p.flags.json, true);
});

test("request-json value is kept intact", () => {
  const p = parseArgs(["runs", "create", "--request-json", '{"goal":"x","flow":"design_v1"}']);
  assert.equal(p.flags["request-json"], '{"goal":"x","flow":"design_v1"}');
});

test("auth login flags parse (provider/method/api-key)", () => {
  const p = parseArgs(["auth", "login", "--provider", "openai", "--method", "api-key", "--api-key", "sk-x"]);
  assert.deepEqual(p._, ["auth", "login"]);
  assert.equal(p.flags.provider, "openai");
  assert.equal(p.flags.method, "api-key");
  assert.equal(p.flags["api-key"], "sk-x");
});

test("auth use positionals + model flag", () => {
  const p = parseArgs(["auth", "use", "openai-codex", "--model", "gpt-5-codex"]);
  assert.deepEqual(p._, ["auth", "use", "openai-codex"]);
  assert.equal(p.flags.model, "gpt-5-codex");
});

// Regression: the registry's CODEX family constant is literally "openai-codex"
// (never the bare string "codex") — checking for "codex" silently defaulted
// every `auth login --provider openai-codex` to the api-key method, which sat
// on a hidden prompt instead of starting OAuth (looked like a hang).
test("resolveLoginMethod defaults openai-codex to oauth, by family or by id", () => {
  assert.equal(resolveLoginMethod(undefined, "openai-codex", "openai-codex"), "oauth");
  assert.equal(resolveLoginMethod(undefined, undefined, "openai-codex"), "oauth");
  assert.equal(resolveLoginMethod(undefined, "codex", "some-other-id"), "api-key");
});

test("resolveLoginMethod defaults non-codex providers to api-key", () => {
  assert.equal(resolveLoginMethod(undefined, "openai", "openai"), "api-key");
  assert.equal(resolveLoginMethod(undefined, "anthropic", "anthropic"), "api-key");
});

test("resolveLoginMethod: an explicit --method always wins", () => {
  assert.equal(resolveLoginMethod("api-key", "openai-codex", "openai-codex"), "api-key");
  assert.equal(resolveLoginMethod("oauth", "openai", "openai"), "oauth");
});

// FORGE-92: a bare `process.exit()` right after an async `process.stdout.
// write()` truncates output once it exceeds the pipe buffer -- observed:
// `forge twin list --json | wc -c` silently cut at exactly 131072 bytes
// under Node, while redirecting to a file (a synchronous write) hid the bug
// entirely. `writeAllSync` fixes it by writing synchronously via
// `fs.writeSync` with an EAGAIN retry loop, so nothing is left buffered by
// the time the process exits.
//
// A first attempt instead waited for an *async* write to drain before
// exiting (an empty `write(callback)`, then a `'drain'` listener) -- both
// worked under Node but silently did NOT under Bun's compiled-binary stdout
// stream, which is the runtime `forge` actually ships as (`bun build
// --compile`). Live validation against the real binary caught it: still
// truncated, just at a different threshold (65536 bytes). `writeAllSync`
// sidesteps the whole async-drain-timing question by never buffering in the
// first place, so it's tested here under BOTH runtimes -- under Bun only
// when it's resolvable on PATH (this exact gap is why: a Node-only test
// passed while the shipped artifact stayed broken).
function runsWriteAllSyncScript(size: number): string {
  const commandsPath = path.join(path.dirname(fileURLToPath(import.meta.url)), "commands.ts");
  // Generate the payload *inside* the spawned process rather than embedding
  // it as a literal in the script text -- passed as an argv string it blows
  // past the OS's ARG_MAX (spawn fails with E2BIG well under 1.5 MB).
  return [
    `import { writeAllSync } from ${JSON.stringify(commandsPath)};`,
    `writeAllSync(1, "x".repeat(${size}));`,
    `process.exit(0);`,
  ].join("\n");
}

async function runScriptAndCollectStdout(
  command: string,
  args: string[],
): Promise<{ code: number; bytes: number }> {
  const child = spawn(command, args, { stdio: ["ignore", "pipe", "inherit"] });
  const chunks: Buffer[] = [];
  child.stdout.on("data", (chunk: Buffer) => chunks.push(chunk));
  const code: number = await new Promise((resolve, reject) => {
    child.on("error", reject);
    child.on("close", (c) => resolve(c ?? -1));
  });
  return { code, bytes: Buffer.concat(chunks).length };
}

test("writeAllSync (Node/tsx) delivers output larger than the pipe buffer before exiting", async () => {
  const size = 1_500_000; // well past the observed 128 KiB pipe-buffer cliff
  const { code, bytes } = await runScriptAndCollectStdout(process.execPath, [
    "--import",
    "tsx",
    "--input-type=module",
    "-e",
    runsWriteAllSyncScript(size),
  ]);
  assert.equal(code, 0);
  assert.equal(bytes, size);
});

test("writeAllSync (Bun) delivers output larger than the pipe buffer before exiting", async (t) => {
  const size = 1_500_000;
  try {
    const { code, bytes } = await runScriptAndCollectStdout("bun", [
      "-e",
      runsWriteAllSyncScript(size),
    ]);
    assert.equal(code, 0);
    assert.equal(bytes, size);
  } catch (err) {
    if ((err as NodeJS.ErrnoException)?.code === "ENOENT") {
      t.skip("bun not found on PATH -- this is the runtime the shipped binary actually uses");
      return;
    }
    throw err;
  }
});
