"""Every adapter's host port can be moved with a variable.

The FreeCAD, KiCad and SPICE adapters published fixed host ports (8102, 8103,
8104). On a machine running a second stack that already holds one of them,
``docker compose up`` failed with "port is already allocated", although the
gateway and mcp-http reach the adapters on the compose network and never use
the host port. OCCT already had ``OCCT_PORT``; these follow it.

Structural assertions on docker-compose.yml; they need no Docker.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"

_PORTS = {
    "occt-converter": ("OCCT_PORT", 8100),
    "freecad-adapter": ("FREECAD_PORT", 8102),
    "kicad-adapter": ("KICAD_PORT", 8103),
    "spice-adapter": ("SPICE_PORT", 8104),
}


def _service_block(name: str) -> str:
    text = COMPOSE.read_text(encoding="utf-8")
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  \S|\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, f"docker-compose.yml has no {name} service"
    return match.group(1)


@pytest.mark.parametrize("service", sorted(_PORTS))
def test_the_adapter_host_port_is_a_variable_with_the_old_default(service: str) -> None:
    variable, port = _PORTS[service]
    assert f'"${{{variable}:-{port}}}:{port}"' in _service_block(service)


@pytest.mark.parametrize("service", sorted(_PORTS))
def test_the_example_env_names_the_variable(service: str) -> None:
    variable, port = _PORTS[service]
    assert f"# {variable}={port}" in (ROOT / ".env.example").read_text(encoding="utf-8")
