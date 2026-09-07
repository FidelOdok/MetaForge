.PHONY: check test ci fix lint format typecheck unit contract integration e2e dashboard-check

# MET-735: every composite target in this file used to fail, so nobody could
# follow its own instructions to a passing result -- `typecheck` ran
# `mypy --strict .` (4,210 errors), `contract` ran against a directory that
# does not exist yet, and `integration` passed a --timeout flag with no
# pytest-timeout installed. It was a third definition of the bar, alongside
# CLAUDE.md's prose and .github/workflows/ci.yml, and it agreed with neither.
#
# These targets now mirror the workflow exactly. If you change one, change
# both, or this drifts again.

# Level 0: Static analysis (before every commit)
check: lint format typecheck

lint:
	ruff check .

format:
	ruff format --check .

# MET-733: the same package list CI enforces, and for the same reason -- a
# ratchet. These eight are at zero and must stay there. `mypy .` repo-wide
# reports ~938 errors (mostly tests/ and domain_agents/) and `--strict`
# reports ~4,210; neither is a gate today. Clean a package, add it here and
# in ci.yml in the same PR.
typecheck:
	mypy --follow-imports=silent \
	  digital_twin/ \
	  mcp_core/ \
	  metaforge/ \
	  observability/ \
	  orchestrator/ \
	  shared/ \
	  skill_registry/ \
	  tool_registry/

# Level 1-2: Unit + contract tests (before every PR)
test: unit contract

unit:
	pytest tests/unit/ -x --tb=short -q

# Guarded exactly as ci.yml guards it: Contract Tests are Phase 2+ scope, so
# tests/contract/ is legitimately absent and its absence must not fail a
# developer's `make test`.
contract:
	@if [ -d "tests/contract" ]; then \
	  pytest tests/contract/ -v --tb=short; \
	else \
	  echo "No contract tests directory found, skipping (Phase 2+ scope)"; \
	fi

# Level 3: Integration tests (MET-730: CI runs these now too)
integration:
	@if [ -d "tests/integration" ]; then \
	  pytest tests/integration/ --tb=short -q --timeout=120; \
	else \
	  echo "No integration tests directory found, skipping"; \
	fi

# Level 8: End-to-end (MET-730: also newly in CI)
e2e:
	@if [ -d "tests/e2e" ]; then \
	  pytest tests/e2e/ --tb=short -q --timeout=120; \
	else \
	  echo "No e2e tests directory found, skipping"; \
	fi

# Everything: check + test + integration + e2e (before merge)
ci: check test integration e2e

# Auto-fix: ruff check --fix + ruff format
fix:
	ruff check --fix .
	ruff format .

# Dashboard checks (MET-731: the 235 vitest tests, not just the type check)
dashboard-check:
	cd dashboard && npx tsc --noEmit
	cd dashboard && npx vitest run
