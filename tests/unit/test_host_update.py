"""An image-only host gets every file the stack mounts, and nothing else (FORGE-593).

``scripts/host_update.sh`` fetches a deploy bundle instead of a checkout. If
compose starts mounting a new repo file and the bundle does not carry it,
the host's mount points at nothing and the service starts without its
config, so the two lists are kept in step here rather than by memory.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "host_update.sh"
COMPOSE = REPO_ROOT / "docker-compose.yml"


def _array(name: str) -> list[str]:
    text = SCRIPT.read_text(encoding="utf-8")
    body = re.search(rf"^{name}=\((.*?)\)", text, re.S | re.M)
    assert body, f"{name}=( ... ) not found in host_update.sh"
    return re.findall(r'"([^"]+)"', body.group(1))


def _mounted() -> set[str]:
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    found: set[str] = set()
    for svc in services.values():
        for vol in svc.get("volumes") or []:
            source = vol.split(":")[0] if isinstance(vol, str) else vol.get("source", "")
            if str(source).startswith("./"):
                found.add(str(source)[2:])
    return found


def test_the_bundle_carries_every_mounted_file() -> None:
    carried = set(_array("BUNDLE")) | set(_array("OUTPUT_DIRS"))
    missing = _mounted() - carried
    assert not missing, f"compose mounts {sorted(missing)} but the host bundle lacks them"


def test_the_bundle_carries_nothing_compose_does_not_need() -> None:
    extra = set(_array("BUNDLE")) - _mounted() - {"docker-compose.yml"}
    assert not extra, f"host bundle ships {sorted(extra)}, which nothing mounts"


def test_every_bundle_path_exists() -> None:
    for path in _array("BUNDLE"):
        assert (REPO_ROOT / path).exists(), f"bundle names {path}, which is not in the repo"


def test_no_source_code_is_in_the_bundle() -> None:
    for path in _array("BUNDLE"):
        assert not path.endswith(".py"), path
        assert path.split("/")[0] not in {
            "api_gateway",
            "orchestrator",
            "twin_core",
            "mcp_core",
            "tool_registry",
            "dashboard",
        }, path


def test_the_script_parses() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
