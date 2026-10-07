"""No service may ship with a credential anyone can read (FORGE-556, FORGE-558).

A secret scan of this repository's public history found
``GRAFANA_PASSWORD=metaforge`` present since the first compose commit. It was
not only an example: ``docker-compose.observability.yml`` hardcoded it with no
override at all, and ``docker-compose.yml`` defaulted to it. Every MetaForge
Grafana therefore shared an admin password printed in a public repository,
while ``GF_AUTH_ANONYMOUS_ENABLED=true`` meant that password was the only thing
separating "read the dashboards" from "administer the instance".

The shipped default also defeated the fix that already existed.
``scripts/onboarding.sh`` generates strong secrets with ``set_if_blank``, which
returns early for any key that already has a value — so a non-empty
``GRAFANA_PASSWORD`` in ``.env.example`` guaranteed the generator never fired.

Grafana was found first, but the pattern was repo-wide: ``NEO4J_PASSWORD`` and
``POSTGRES_PASSWORD`` defaulted to ``metaforge``, ``MINIO_SECRET_KEY`` to
``minioadmin``, and Temporal's database hardcoded ``temporal`` on both sides.
Those three publish host ports by default with no profile gate, so they were
*more* exposed than Grafana, which at least sat behind ``--profile
observability``.

These are structural assertions on the compose files and ``.env.example``; they
need no Docker. The point is that the next person to add a convenient default
has to delete a test to do it.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = [
    REPO_ROOT / "docker-compose.yml",
    REPO_ROOT / "docker-compose.observability.yml",
]
ENV_EXAMPLE = REPO_ROOT / ".env.example"

#: Each credential the compose files must take from the environment, mapped to
#: the published default it used to carry. Named so a reintroduction fails by
#: name rather than by a vague assertion.
CREDENTIALS = {
    "GRAFANA_PASSWORD": "metaforge",
    "NEO4J_PASSWORD": "metaforge",
    "POSTGRES_PASSWORD": "metaforge",
    "MINIO_SECRET_KEY": "minioadmin",
    "TEMPORAL_POSTGRES_PASSWORD": "temporal",
}

#: Identifiers rather than secrets. These keep their defaults on purpose, and
#: the tests below must not flag them.
NOT_SECRETS = {"POSTGRES_USER", "POSTGRES_DB", "NEO4J_USER", "MINIO_ACCESS_KEY", "MINIO_BUCKET"}


COMPOSE_FILES = [
    REPO_ROOT / "docker-compose.yml",
    REPO_ROOT / "docker-compose.override.yml",
    REPO_ROOT / "docker-compose.observability.yml",
]

#: ``${VAR:?message}`` — refuse. ``${VAR:-value}`` — fall back to a shared one.
REQUIRED = re.compile(r"\$\{(\w+):\?")
FALLBACK = re.compile(r"\$\{(\w+):-")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code_lines(path: Path) -> list[str]:
    """Lines excluding comments — commentary may name the old defaults."""
    return [ln for ln in _text(path).splitlines() if not ln.strip().startswith("#")]


class TestNoPublishedCredentials:
    def test_no_credential_falls_back_to_a_default(self):
        """A default is a password every reader of this repository knows."""
        for path in COMPOSE_FILES:
            for line in _code_lines(path):
                for var in FALLBACK.findall(line):
                    assert var in NOT_SECRETS or var not in CREDENTIALS, (
                        f"{path.name} gives {var} a fallback: {line.strip()!r}. "
                        "A shared default here is a credential everyone has — "
                        "refuse to start instead."
                    )

    def test_every_credential_is_required_where_it_is_used(self):
        """Each must appear as ``${VAR:?...}`` somewhere, not merely absent."""
        required = set()
        for path in COMPOSE_FILES:
            for line in _code_lines(path):
                required.update(REQUIRED.findall(line))
        for var in CREDENTIALS:
            assert var in required, (
                f"{var} is never required with ':?' in any compose file. Either it "
                "regained a default or its service was dropped — check which."
            )

    def test_the_published_values_appear_nowhere_in_code(self):
        for path in COMPOSE_FILES:
            for line in _code_lines(path):
                for var, leaked in CREDENTIALS.items():
                    for form in (f"{var}={leaked}", f"{var}: {leaked}"):
                        assert form not in line, (
                            f"{path.name} reintroduces the published value for {var}"
                        )

    def test_no_connection_string_embeds_a_password(self):
        """A DSN default is just as public as a bare password was."""
        for path in COMPOSE_FILES:
            for line in _code_lines(path):
                assert "metaforge:metaforge@" not in line, (
                    f"{path.name} embeds credentials in a connection string: "
                    f"{line.strip()!r}. Build it from the variables instead."
                )

    def test_temporals_own_database_is_not_hardcoded(self):
        """It is unpublished, but 'temporal/temporal' was still shared by all."""
        for line in _code_lines(REPO_ROOT / "docker-compose.yml"):
            stripped = line.strip()
            assert stripped not in ("POSTGRES_PASSWORD: temporal", "- POSTGRES_PWD=temporal"), (
                "Temporal's database password is hardcoded again"
            )


class TestOnboardingCanStillGenerateThem:
    def test_env_example_ships_every_credential_blank(self):
        """Non-empty means ``set_if_blank`` returns early and never fires."""
        text = _text(ENV_EXAMPLE)
        for var in CREDENTIALS:
            assert re.search(rf"^{var}=$", text, re.MULTILINE), (
                f".env.example must ship {var} blank. onboarding.sh's set_if_blank "
                "skips keys that already have a value, so any default here silently "
                "disables the secret generator — which is exactly how the published "
                "passwords survived having a generator written to replace them."
            )

    def test_onboarding_generates_every_credential(self):
        script = _text(REPO_ROOT / "scripts" / "onboarding.sh")
        for var in CREDENTIALS:
            assert f'set_if_blank {var} "$(rand_secret)"' in script, (
                f"onboarding.sh must generate {var} — compose now refuses to start without it."
            )

    def test_generation_is_not_gated_on_a_mode(self):
        """Compose refuses in both modes, so both must get credentials."""
        script = _text(REPO_ROOT / "scripts" / "onboarding.sh")
        generation = script.index('set_if_blank NEO4J_PASSWORD "$(rand_secret)"')
        mode_branch = script.index('if [ "$MODE" = "usage" ]', generation - 4000)
        assert generation < mode_branch, (
            "credential generation moved inside a mode branch; a --develop install "
            "would then fail every docker compose command"
        )
