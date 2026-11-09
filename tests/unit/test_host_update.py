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


# --- Signature verification (FORGE-823) -------------------------------------
#
# Keyless first, exactly as before. A key signature, made by CI only when the
# public Rekor log is down, is accepted only against a public key the operator
# placed on the host. These drive ``verify_signature`` with a fake cosign.

LIB = REPO_ROOT / "scripts" / "lib" / "verify_signature.sh"
DIGEST = "ghcr.io/fidelodok/metaforge-gateway@sha256:" + "b" * 64


def _verify(
    tmp_path: Path, *, keyless_rc: int, key_rc: int, pubkey: bool
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    calls = tmp_path / "calls"
    fake = tmp_path / "cosign"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{calls.as_posix()}"\n'
        f'case "$*" in *--key*) echo "key: no matching signatures" >&2; exit {key_rc} ;; esac\n'
        'echo "keyless: no matching signatures" >&2\n'
        f"exit {keyless_rc}\n",
        encoding="utf-8",
        newline="\n",
    )
    fake.chmod(0o755)
    key = tmp_path / "cosign.pub"
    if pubkey:
        key.write_text("-----BEGIN PUBLIC KEY-----\n", encoding="utf-8")
    script = (
        f'. "{LIB.as_posix()}"; '
        f'COSIGN="{fake.as_posix()}"; PUBKEY="{key.as_posix()}"; '
        r"IDENTITY='^https://github\.com/FidelOdok/MetaForge/'; "
        "ISSUER=https://token.actions.githubusercontent.com; "
        f'verify_signature "{DIGEST}"'
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30)
    lines = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return result, lines


def test_a_keyless_signature_passes_without_looking_at_the_key(tmp_path: Path) -> None:
    result, calls = _verify(tmp_path, keyless_rc=0, key_rc=1, pubkey=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"verified  {DIGEST}"
    assert len(calls) == 1
    assert "--certificate-oidc-issuer https://token.actions.githubusercontent.com" in calls[0]
    assert "--key" not in calls[0]


def test_a_key_signature_passes_when_the_host_pins_the_key(tmp_path: Path) -> None:
    result, calls = _verify(tmp_path, keyless_rc=1, key_rc=0, pubkey=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"verified (key, no transparency log)  {DIGEST}"
    assert len(calls) == 2
    assert calls[1] == (
        f"verify --key {(tmp_path / 'cosign.pub').as_posix()} --insecure-ignore-tlog=true {DIGEST}"
    )


def test_both_failing_stops_the_update(tmp_path: Path) -> None:
    result, calls = _verify(tmp_path, keyless_rc=1, key_rc=1, pubkey=True)

    assert result.returncode == 1
    assert f"SIGNATURE CHECK FAILED for {DIGEST}; nothing restarted" in result.stderr
    assert len(calls) == 2


def test_without_a_pinned_key_only_keyless_counts(tmp_path: Path) -> None:
    # The host behaves exactly as before FORGE-823 until an operator places
    # cosign.pub: a key signature is never even tried.
    result, calls = _verify(tmp_path, keyless_rc=1, key_rc=0, pubkey=False)

    assert result.returncode == 1
    assert "SIGNATURE CHECK FAILED" in result.stderr
    assert len(calls) == 1
    assert "--key" not in calls[0]


def test_the_public_key_never_rides_in_the_bundle() -> None:
    """The key's trust must not come from the same fetch it vouches for."""
    assert not any("cosign" in path for path in _array("BUNDLE"))
    text = SCRIPT.read_text(encoding="utf-8")
    assert "METAFORGE_COSIGN_PUBKEY" in text
    assert "--no-verify" in text


def test_the_verify_library_parses() -> None:
    subprocess.run(["bash", "-n", str(LIB)], check=True)
