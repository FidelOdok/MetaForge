# Tier-1 — CLI error-paths scenarios

Validates: MET-410 (sub-deliverable F1b of MET-409).
Tier: 1
Run: `/uat-cycle12 --tier 1 --scenario cli-error-paths`

Two scenarios that promote the 🔄 NEW catalog rows KB-CLI-005 and
KB-CLI-006 from `docs/uat/kb-test-plan.md` §3 into executable tier-1
form. Both rows exercise the `forge ingest` CLI's user-facing error
reporting on invalid input. The backing implementation already shipped
under L1-C2 (`fix(cli): actionable errors on bad ingest input`,
MET-411, PR #175) — these scenarios make the behaviour observable
under `/uat-cycle12 --tier 1`.

The scenarios assume the `forge` CLI is on `$PATH` (or invokable via
`python -m cli.forge_cli.main` from the repo root) and that the
MetaForge gateway is **not** required — both flows fail or warn
locally before any network call.

> **Note:** These scenarios do not need fixture files committed to the
> repo. KB-CLI-005 uses an inline nonexistent path string; KB-CLI-006
> creates throw-away binary / empty files inline under `$TMPDIR` and
> cleans up. This keeps the tier-1 fixture surface narrow.

---

## Scenario: KB-CLI-005 — CLI rejects nonexistent path
Validates: MET-385, MET-411
Tier: 1

### Given
- The `forge` CLI is invokable as a subprocess (non-interactive).
- A path that is guaranteed not to exist on the runner's filesystem,
  e.g. `/does/not/exist` (or any `$TMPDIR/forge-uat-missing-<rand>`
  the runner generates fresh and does not create).

### When
1. Run `forge ingest /does/not/exist` as a subprocess (capture stdout,
   stderr, and exit code; do not pass `--help`).

### Then
- The subprocess exits with code `2` (CLI input error — distinct from
  `0` success and `1` runtime/server error).
- stderr contains the literal phrase `path does not exist:
  /does/not/exist` in an `Error:`-prefixed line. The exact path passed
  on the command line appears in the message.
- stderr contains **no** Python traceback — no `Traceback (most recent
  call last):`, no module/file path lines from `cli/forge_cli/`, no
  `FileNotFoundError:` raw class name.
- No partial ingest is committed: a follow-up read of
  `metaforge://knowledge/sources` lists no entry whose `source_path`
  references `/does/not/exist`.

---

## Scenario: KB-CLI-006 — CLI handles binary / empty files gracefully
Validates: MET-336, MET-385, MET-411
Tier: 1

### Given
- A throw-away working directory under `$TMPDIR` (e.g.
  `$TMPDIR/forge-uat-cli-006-<rand>/`) containing four files that the
  runner writes inline:
  - `note.md` — a valid one-line markdown file
    (`"# tier1 cli-006 valid marker"`).
  - `blob.bin` — 256 bytes of pseudo-random binary
    (unsupported extension; should be silently filtered).
  - `binary.txt` — a `.txt` file whose body contains an embedded NUL
    byte (`b"hello\x00world"`) so the binary-sniff heuristic trips on
    a text-ish extension.
  - `empty.md` — a zero-byte markdown file.
- The CLI is invokable as a subprocess.

### When
1. Run `forge ingest <tmpdir>` (recursive directory walk) as a
   subprocess; capture stdout, stderr, and exit code.

### Then
- The subprocess exits with code `0` (continue-on-error: the run
  completes despite per-file warnings).
- stderr contains a `warning: skipping binary file` line referencing
  `binary.txt` (the text-extension-with-NUL case caught by the
  pre-flight sniff).
- stderr contains a `warning: skipping empty file` line referencing
  `empty.md`.
- `blob.bin` is **not** mentioned in any warning — files with
  unsupported extensions are silently filtered by
  `SUPPORTED_EXTENSIONS` and never attempted.
- The CLI's per-file summary records `binary.txt` and `empty.md` under
  `skipped` (with reasons `binary content` and `empty file`) and
  reports `note.md` as successfully dispatched with
  `chunks_indexed >= 1`.
- stderr contains **no** Python traceback — no `Traceback (most recent
  call last):` line and no raw exception class name leaks.

---

## Acceptance

- Both scenarios PASS in a single `/uat-cycle12 --tier 1 --scenario
  cli-error-paths` invocation.
- Master-plan rows KB-CLI-005 and KB-CLI-006 (`docs/uat/kb-test-plan.md`
  §3) record ✅ PASS in the next run report.
