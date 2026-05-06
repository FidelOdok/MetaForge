# Implement the next L1 item

Read `docs/plans/l1-implementation.md` and execute exactly **one**
unblocked item from its Status board.

## Context-isolation rule (MANDATORY)

The actual implementation of each item MUST run in a freshly-spawned
**sub-agent** so context does not carry across iterations. Reasons:

- Determinism — the Nth item's output should not depend on the (N-1)th
  item's working memory.
- Reviewability — each PR is a clean unit produced from the same
  starting state (the spec + the repo).
- Bounded blast radius — a confused agent in one iteration cannot
  pollute the next.

**The outer context** (this slash command) is responsible only for:
pre-flight, item selection, plan-file edits, sub-agent dispatch, and
PR opening based on the sub-agent's structured result. It must NOT
read implementation files, write code, run pytest, or open the PR's
code commit itself — those are the sub-agent's job.

**The sub-agent** is invoked via the Agent tool with:
- `subagent_type`: `general-purpose`
- `description`: `"L1 item <id> — <title>"` (≤ 5 words)
- `prompt`: see "Sub-agent prompt template" below
- (no isolation parameter — the sub-agent works on the same checkout
  but starts with a fresh context window)

## Per-iteration contract (do these in order)

1. **Load** `docs/plans/l1-implementation.md`. Find the topmost row in
   the Status board where:
   - `Status == ⏳ Pending`, AND
   - every id in `Deps` has `Status == ✅` (or column is `—`).

2. **If no such row exists**, append a Run-history entry to the plan
   file (template at the bottom of the plan), commit it on a branch
   `chore/l1-loop-final-report`, push, and open a PR with title
   `chore(plan): L1 loop final report`. Then exit with a summary
   message: "L1 loop complete. N PRs opened, M items remain blocked."

3. **If the row carries `Requires human decision: true`**:
   - Update its `Status` to `⏸️ blocked: human-decision`.
   - Commit only the plan-file change on branch
     `chore/l1-loop-mark-<id>-blocked`, push, open a PR with title
     `chore(plan): mark <id> blocked on human decision`.
   - Exit with: "Item <id> needs a human decision before the loop can
     proceed. See item spec in the plan."

4. **Otherwise dispatch the item to a sub-agent.** For the chosen row:
   1. Set `Status` to `🔧 In progress` and write the branch name into
      the row's `Branch` column. Save and commit only the plan-file
      change on `main` with message `chore(plan): start <id>` then
      cherry-pick that single commit onto the new feature branch (so
      the plan stays consistent on main while the sub-agent works).
      Actually simpler: leave the plan edit unstaged for now; we'll
      land the status update at the same time we mark ✅.
   2. Run `git checkout -b <branch>` from `main`.
   3. **Spawn the sub-agent** via the Agent tool with the prompt
      below ("Sub-agent prompt template"). Wait for it to return.
   4. **Process the sub-agent's structured result** (see
      "Sub-agent return contract" below):
      - On `result == "PASS"`: status becomes ✅, PR url is in the
        result.
      - On `result == "FAIL"`: status becomes ❌ with the failure
        reason. The sub-agent has already committed/pushed whatever
        partial work is salvageable; if it opened a draft PR, use
        that url, otherwise leave the PR column empty.
      - On `result == "BLOCKED_CLARIFICATION"`: status becomes ⏸️
        with the clarification question in the row's Notes column.
        No PR.
   5. **Update the plan file** with the new Status, Branch, and PR
      columns for the chosen row only. Commit the plan update onto
      the same feature branch (or onto `main` for the BLOCKED case)
      with message `chore(plan): mark <id> <status>`. Push.
   6. **If the sub-agent opened a PR**, edit it to append a link
      back to `docs/plans/l1-implementation.md#<id>` and the plan-
      update commit; this is the only PR-body edit the outer agent
      makes.
   7. Exit with a one-line summary:
      `<id>: <PASS|FAIL|BLOCKED>. PR: <url or '—'>. Next iteration will pick up.`

## Sub-agent prompt template

The Agent tool invocation in step 4.3 above uses this prompt verbatim,
with `<<placeholders>>` filled from the item spec:

```
You are implementing one L1 item for the MetaForge project. Work in
the current checkout — you do NOT need to spawn further agents.

## Your task

Item id: <<L1-ID>>
Title:    <<TITLE>>
MET id:   <<MET-XXX>>
Branch:   <<BRANCH>> (already created and checked out)

## Spec — implement EXACTLY this, no scope creep

<<COPY THE FULL SPEC BLOCK FROM docs/plans/l1-implementation.md>>

## Files to touch

<<LIST FROM SPEC>>

## Tests required

<<LIST FROM SPEC>>

## Acceptance — done when

<<COPY "Done when" FROM SPEC>>

## Workflow you must follow

1. Read the existing files you will modify so your edits are minimal.
2. Implement per spec. Do NOT touch files outside the spec's "Files"
   list. If something forces you to (a missing import, an unrelated
   bug discovered en route), stop and return BLOCKED_CLARIFICATION
   with the question — do not silently expand scope.
3. Add the tests named in the spec.
4. Run the validation gauntlet on the TOUCHED paths only:
     pytest <test paths from spec>
     ruff check <touched paths>
     mypy <touched paths>
   Retry up to 3 times on failure. Read the failure each time and
   attempt a real fix; do not skip tests.
5. If validation passes:
   - Commit with conventional-commits message:
       <type>(<scope>): <title> (<MET-XXX>)

       <one-paragraph summary>

       Item id: <<L1-ID>>
       Linear:  <<MET-XXX>>
   - Push: git push -u origin <<BRANCH>>
   - Open the PR with gh pr create. Title = commit subject. Body:
       ## Item
       <<L1-ID>> — <<TITLE>> (<<MET-XXX>>)

       ## Spec
       <copy spec verbatim>

       ## Tests
       <list of tests added + green pytest summary>

       ## Validation
       pytest: ✅ N passed
       ruff:   ✅
       mypy:   ✅

       🤖 Implemented by /loop /implement-l1-next sub-agent
6. If validation fails after 3 retries:
   - Open a DRAFT PR with the partial work and the failure log.
   - Return result FAIL.

## What you return

Your final message MUST be a single fenced JSON block with this shape
and nothing else:

```json
{
  "result": "PASS" | "FAIL" | "BLOCKED_CLARIFICATION",
  "branch": "<<BRANCH>>",
  "pr_url": "<the url from gh pr create>" | null,
  "summary": "<one sentence — what you did or why blocked>",
  "files_touched": ["<path1>", "<path2>", ...],
  "tests_added": ["<test path::test_name>", ...],
  "validation": {
    "pytest": "<pass count>" | "FAIL: <reason>",
    "ruff":   "PASS" | "FAIL: <count>",
    "mypy":   "PASS" | "FAIL: <count>"
  },
  "blocker_question": "<only if BLOCKED_CLARIFICATION; the exact question>"
}
```
```

## Sub-agent return contract

The outer context parses the JSON and acts on `result`:

- `PASS` → status ✅, PR url recorded.
- `FAIL` → status ❌ with `summary` as the reason; PR url recorded if
  draft was opened.
- `BLOCKED_CLARIFICATION` → status ⏸️ with `blocker_question` in the
  Notes column; no PR.

If the sub-agent's final message is NOT a parseable JSON block, the
outer context treats this as `FAIL` with reason
`"sub-agent did not return structured result"`.

## Rules the loop must respect

- **One item per iteration.** Even if the implementation looks
  trivial, exit after the PR. The human merges, then the loop fires
  again.
- **Never merge to main locally.** PR is the only landing path.
- **Never commit secrets.** Skip files matching `.env*`, `*credentials*`,
  `id_rsa*`, etc., even if the spec touches the surrounding directory.
- **Conventional commits, with Linear id mandatory.** Format above.
- **Don't edit other items' rows.** Only the chosen item's row.
- **Don't invent items.** If the spec is ambiguous, set
  `Status = ⏸️ blocked: clarification` and exit with a question.
- **Multi-iteration items (L1-E4, L1-F1).** If the chosen row is one
  of these, follow the sub-iteration recipe in the plan: create the
  sub-rows in the Status board on first iteration, then the loop
  picks up sub-rows in subsequent iterations.

## Stop conditions for the loop runner

The runner (i.e., `/loop /implement-l1-next`) should stop when:
- Three consecutive iterations end in ❌ or ⏸️ — surface the reasons
  and stop self-pacing.
- The plan's Status board has no ⏳ rows left.
- 8 hours of wall-clock have elapsed since the loop started.

## Pre-flight (run on every iteration before step 1)

- **Working tree clean (except for known-local files).** Run
  `git status --porcelain` and ignore lines whose path is `.mcp.json`
  — that file is intentionally allowed to carry machine-local edits
  (venv-specific Python path, locally-enabled adapters). If any other
  line remains, abort with: "working tree dirty — resolve before
  running the loop again." See "Why .mcp.json is excluded" below.
- Current branch is `main`? If not, abort with: "expected to start
  from main; checked out branch is X."
- `pytest --collect-only -q` returns 0 errors? If not, abort —
  baseline tests are broken, not safe to add more.
- `gh auth status` returns OK? If not, abort — can't open PRs without
  the GitHub CLI authenticated.

### Why `.mcp.json` is excluded from the dirty-tree check

`.mcp.json` is the launcher Claude Code uses to start the MetaForge
MCP server. Engineers commonly need machine-local edits to it — e.g.
`.venv/bin/python` instead of system `python`, or a different
`METAFORGE_ADAPTERS` set depending on which adapters are wired
locally. Those edits should NOT be committed (they would break other
contributors), but they also should NOT block the loop. The right
long-term fix is to untrack `.mcp.json` and ship a
`.mcp.json.example` template; until that lands, the pre-flight
treats `.mcp.json` as if it were untracked.
