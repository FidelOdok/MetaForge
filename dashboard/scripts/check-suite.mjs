/**
 * Run the suite and fail if fewer test files ran than exist (FORGE-389).
 *
 * Vitest drops a test file whose worker exceeds its 60-second start
 * timeout, which happens on a checkout served over a slow filesystem. The
 * summary then reads:
 *
 *   Test Files  64 passed (64)
 *
 * with no mention of the three that never ran. The parenthesised total is
 * how many were collected, not how many exist, so a run that skipped part
 * of the suite is indistinguishable from a complete one by eye. Measured
 * across several runs here: 57, 46 and 9 of 67 files, each reporting every
 * file it did run as passing.
 *
 * The exit code is non-zero in that case, so CI is not fooled. A person
 * reading the summary is, which is the gap this closes.
 */
import { spawnSync } from 'node:child_process';
import { globSync, readFileSync, rmSync } from 'node:fs';
import { dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const REPORT = resolve(ROOT, 'node_modules/.vitest-suite-report.json');

const onDisk = globSync('src/**/*.test.{ts,tsx}', { cwd: ROOT }).sort();
if (onDisk.length === 0) {
  console.error('check-suite: found no test files at all — is the glob still right?');
  process.exit(1);
}

const run = spawnSync(
  'npx',
  [
    'vitest',
    'run',
    '--reporter=default',
    '--reporter=json',
    '--outputFile',
    REPORT,
    ...process.argv.slice(2),
  ],
  { cwd: ROOT, stdio: 'inherit' },
);

let ran = [];
try {
  const report = JSON.parse(readFileSync(REPORT, 'utf8'));
  ran = (report.testResults ?? []).map((r) => relative(ROOT, r.name)).sort();
} catch (err) {
  console.error(`check-suite: could not read the run report (${err.message})`);
  process.exit(run.status === 0 ? 1 : (run.status ?? 1));
} finally {
  try {
    rmSync(REPORT);
  } catch {
    /* the report is a convenience, not a result */
  }
}

const missing = onDisk.filter((f) => !ran.includes(f));
if (missing.length > 0) {
  console.error(
    `\ncheck-suite: ${missing.length} of ${onDisk.length} test files did not run.\n` +
      'Whatever the summary above says, this suite did not finish. Usually a\n' +
      "worker exceeded vitest's 60s start timeout — look for \"Failed to start\"\n" +
      'above. Re-running often gets a different subset.\n\n' +
      missing.map((f) => `  - ${f}`).join('\n') +
      '\n',
  );
  process.exit(1);
}

if (run.status !== 0) {
  process.exit(run.status ?? 1);
}
console.log(`\ncheck-suite: all ${onDisk.length} test files ran.`);
