// Rewrites src/build-info.ts from the current git commit + date + package
// version. Run by the bundle scripts before packaging so the artifact can
// report its provenance (`forge-tui --version`).
import { execSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");

let commit = "unknown";
try {
  commit = execSync("git rev-parse --short HEAD", { cwd: root }).toString().trim();
} catch {
  // not a git checkout — leave "unknown"
}

const date = new Date().toISOString().slice(0, 10);
const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));

// Prefer the git tag so a release binary reports its real version (e.g. v0.1.6
// -> "0.1.6") without anyone remembering to bump package.json. Falls back to
// package.json when no tag is reachable (dev checkouts).
let version = pkg.version ?? "0.0.0";
try {
  const tag = execSync("git describe --tags --abbrev=0", {
    cwd: root,
    stdio: ["pipe", "pipe", "ignore"],
  })
    .toString()
    .trim();
  if (tag) version = tag.replace(/^v/, "");
} catch {
  // no tags reachable — keep the package.json version
}

const content = `/**
 * Build stamp. Committed with dev defaults; \`npm run stamp\` (run by the bundle
 * scripts) rewrites it from git + date so a shipped binary reports exactly which
 * commit it was built from — the missing signal behind the stale-CLI trap.
 */
export const BUILD = {
  version: "${version}",
  commit: "${commit}",
  date: "${date}",
} as const;
`;

writeFileSync(join(root, "src", "build-info.ts"), content);
console.error(`stamped build-info.ts: ${version} (${commit}, ${date})`);
