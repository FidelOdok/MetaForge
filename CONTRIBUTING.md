# Contributing to MetaForge

Thanks for wanting to help. MetaForge turns engineer intent into
reviewable, manufacturable hardware deliverables, and the prime rule
applies to the codebase as much as to its output: **if it can't be
versioned, reviewed, and built, it doesn't ship.**

By contributing you agree that your work is licensed under the
[Apache License 2.0](LICENSE), and that you have the right to submit it.
Everyone taking part is covered by our
[Code of Conduct](CODE_OF_CONDUCT.md).

## Get set up

One script takes a clean machine to a running instance:

```bash
./scripts/install.sh --develop
```

Develop mode gives you an editable install in a `.venv`, hot-reload
containers, and the source bind-mounted into the stack. It is idempotent,
so re-run it whenever you want to add a tool adapter. `--help` lists every
flag.

By hand, if you prefer:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,knowledge,cadquery]"
docker compose up -d postgres neo4j gateway dashboard
```

## Where to start

- **[`CLAUDE.md`](CLAUDE.md)** is the deep guide: architecture, the
  12-level testing taxonomy, the observability requirements, phase scope.
  Read it before your first change.
- **[`docs/architecture.md`](docs/architecture.md)** describes the system
  as it is actually built today.
- **[`docs/capability-matrix.md`](docs/capability-matrix.md)** is the
  inventory of what exists, and what is deliberately out of scope for
  Phase 1.

Be careful with phase scope. Phase 1 is 6-7 specialist agents over
electronics-heavy products, with KiCad **read-only**. Schematic generation
is Phase 2. A PR that blurs those lines will be asked to change.

## Issues

Day-to-day planning happens in a private Jira project, so `FORGE-<n>`
references in commits and comments will not resolve for you. That is
expected and nothing is hidden behind them that matters for reviewing a
change. (`MET-<n>` references are older still, from before the move off
Linear.)

**Use [GitHub Issues](https://github.com/FidelOdok/MetaForge/issues)** to
report a bug or propose a feature. Open one before a large change, so we
can agree on the shape before you spend the effort.

## Making a change

Never commit to `main`.

1. **Branch** off `main` — `feat/short-description`, `fix/short-description`.
2. **Implement**, with tests and observability (below).
3. **Update the docs in the same PR**, never as a follow-up.
4. **Run the checks** (below) until they pass.
5. **Open a PR** describing what changed and why.

### Commit messages

[Conventional Commits](https://www.conventionalcommits.org/):

```
type(scope): description
```

Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `ci`, `style`,
`perf`, `build`. Scope is the module — `twin-core`, `skill-system`, `mcp`,
`cli`, `gateway`, `orchestrator`. For example:

```
feat(twin-core): implement Twin API facade
fix(skill-system): handle missing definition.json gracefully
```

This is not cosmetic: the release workflow reads the PR title to decide
the version bump.

### The bar before you open a PR

```bash
make check         # ruff check, ruff format --check, mypy
make test          # unit + contract tests
make integration   # integration tests
```

`make ci` runs all of it, and mirrors `.github/workflows/ci.yml` exactly.
Two things that catch people out:

- **`ruff check` passing is not enough.** CI runs `ruff format --check`
  as well, and it fails independently. `make fix` applies both.
- **mypy is a ratchet, not a blanket.** Eight packages are at zero errors
  and must stay there; the list lives in both the `Makefile` and
  `ci.yml`. If you clean another package, add it to both lists in the same
  PR. Nothing runs under `--strict`.

Touching the dashboard:

```bash
make dashboard-check          # tsc, eslint, and the full vitest suite
cd dashboard && npm test      # the suite on its own
```

Run `npm test`, not `npx vitest run` — the raw runner silently skips
files, and `npm test` asserts that every test file actually executed.

Touching docs:

```bash
cd docs-site && npm run build && npm test
```

The build fails on a broken link or cross-reference, and the test asserts
that every URL the site published before the Docusaurus switch still
resolves.

### What every module needs

**Tests.** The minimum for Phase 1 is unit tests for public functions
(`tests/unit/`), component tests through the in-memory doubles, integration
tests for cross-module wiring (`tests/integration/`), and at least one
end-to-end vertical per agent (`tests/e2e/`).

A test that passes without asserting anything is worse than no test. If
you are not sure yours is real, break the code on purpose and confirm it
goes red.

**Observability.** Instrument new Python modules with structured logging
(`structlog`), metrics (`observability.metrics`), and tracing
(`observability.tracing`, with `span.record_exception(exc)` in except
blocks). Everything degrades gracefully when the OpenTelemetry SDK is
absent. If you add a metric, add its alert rule to
`observability/alerting/rules.yaml` — version-controlled, never an ad-hoc
Grafana alert.

**Generated files.** Everything under `integrations/` and
`.claude-plugin/marketplace.json` is written by
`scripts/build_integrations.py`. Edit the generator and re-run it; a
hand-edit is overwritten on the next build. Likewise
`docs/reference/openapi.json` comes from `python scripts/gen_openapi.py`.

## Security

Please do not open a public issue for a vulnerability. Email
fidel.odok@metaforge.uk instead, and give us a chance to ship a fix before
it is disclosed.
