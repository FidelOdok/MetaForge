"""Grafana must not ship with a password anyone can read (FORGE-556).

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

These are structural assertions on the compose files and ``.env.example``; they
need no Docker. The point is that the next person to add a convenient default
has to delete a test to do it.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = [
    REPO_ROOT / "docker-compose.yml",
    REPO_ROOT / "docker-compose.observability.yml",
]
ENV_EXAMPLE = REPO_ROOT / ".env.example"

#: The value that was public. Named so a reintroduction fails by name.
LEAKED_DEFAULT = "metaforge"


def _grafana_env(path: Path) -> list[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    grafana = data["services"]["grafana"]
    env = grafana["environment"]
    # Both list and mapping forms are valid compose; normalise to "K=V".
    if isinstance(env, dict):
        return [f"{k}={v}" for k, v in env.items()]
    return list(env)


def _admin_password(path: Path) -> str:
    for entry in _grafana_env(path):
        if entry.startswith("GF_SECURITY_ADMIN_PASSWORD="):
            return entry.split("=", 1)[1]
    raise AssertionError(f"{path.name} does not set GF_SECURITY_ADMIN_PASSWORD")


class TestNoPublishedPassword:
    def test_every_compose_file_takes_the_password_from_the_environment(self):
        for path in COMPOSE_FILES:
            value = _admin_password(path)
            assert value.startswith("${GRAFANA_PASSWORD"), (
                f"{path.name} hardcodes Grafana's admin password. It must come from "
                "GRAFANA_PASSWORD, which is generated per install."
            )

    def test_no_compose_file_falls_back_to_a_default(self):
        """``:-`` would reintroduce a shared password; ``:?`` refuses instead."""
        for path in COMPOSE_FILES:
            value = _admin_password(path)
            assert ":-" not in value, (
                f"{path.name} gives GRAFANA_PASSWORD a fallback. A default here is a "
                "password every install shares and every reader of this repository "
                "knows — refuse to start instead."
            )
            assert value.startswith("${GRAFANA_PASSWORD:?"), (
                f"{path.name} must use ':?' so a missing password stops the container "
                "rather than silently selecting one."
            )

    def test_the_leaked_value_appears_nowhere(self):
        for path in [*COMPOSE_FILES, ENV_EXAMPLE]:
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue  # the commentary explaining this fix may name it
                assert f"GRAFANA_PASSWORD={LEAKED_DEFAULT}" not in stripped, (
                    f"{path.name} reintroduces the published Grafana password"
                )


class TestOnboardingCanStillGenerateIt:
    def test_env_example_ships_the_key_blank(self):
        """Non-empty here means ``set_if_blank`` returns early and never fires."""
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        assert re.search(r"^GRAFANA_PASSWORD=$", text, re.MULTILINE), (
            ".env.example must ship GRAFANA_PASSWORD blank. onboarding.sh's "
            "set_if_blank skips keys that already have a value, so any default "
            "here silently disables the secret generator."
        )

    def test_onboarding_generates_it_in_both_modes(self):
        """Compose now refuses without it, so --develop needs one too."""
        script = (REPO_ROOT / "scripts" / "onboarding.sh").read_text(encoding="utf-8")
        assert script.count('set_if_blank GRAFANA_PASSWORD "$(rand_secret)"') == 2, (
            "onboarding.sh must generate GRAFANA_PASSWORD in both usage and develop "
            "mode — compose refuses to start Grafana without it in either."
        )
