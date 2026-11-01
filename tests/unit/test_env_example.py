"""`.env.example` lists every variable docker compose reads.

It is the one place an operator looks to find out what can be set. It had
drifted to 36 of the 93 variables compose substitutes, including the token
that guards the credential routes, so this keeps it honest.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = sorted(ROOT.glob("docker-compose*.yml"))

# `${NAME}`, `${NAME:-default}`, `${NAME:?message}`
_SUBSTITUTION = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(:?[-?][^}]*)?\}")
_REQUIRED = re.compile(r"\$\{([A-Z][A-Z0-9_]*):?\?")
# `NAME=` or `# NAME=value`: a commented line documents the compose default.
_DOCUMENTED = re.compile(r"^#?\s?([A-Z][A-Z0-9_]*)=", re.MULTILINE)
_ACTIVE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$", re.MULTILINE)


def _compose_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in COMPOSE_FILES)


def _example_text() -> str:
    return (ROOT / ".env.example").read_text(encoding="utf-8")


def test_compose_files_are_found() -> None:
    assert ROOT / "docker-compose.yml" in COMPOSE_FILES


def test_every_compose_variable_is_in_env_example() -> None:
    used = set(_SUBSTITUTION.findall(_compose_text()))
    used_names = {name for name, _ in used}
    documented = set(_DOCUMENTED.findall(_example_text()))
    missing = sorted(used_names - documented)
    assert not missing, (
        f"docker compose reads {missing} but .env.example does not list them. "
        "Add each with a one-line comment (commented out at its compose default "
        "if it is optional)."
    )


def test_required_secrets_ship_blank_and_active() -> None:
    """Compose refuses to start without these; install.sh fills a blank `NAME=`.

    A commented line would leave the generator nothing to fill in place, and a
    value would be a password published in a public repository.
    """
    required = set(_REQUIRED.findall(_compose_text()))
    active = dict(_ACTIVE.findall(_example_text()))
    for name in sorted(required):
        assert name in active, f"{name} must be an uncommented `{name}=` line"
        assert active[name] == "", f"{name} must ship blank, not {active[name]!r}"


def test_no_em_dashes() -> None:
    assert "—" not in _example_text()
