"""The object store runs from our own mirrored image, scoped to its users (FORGE-577).

MinIO's community images were discontinued and then deleted from Docker Hub,
and every host that pulled ``minio/minio`` broke at once. These pin the
replacement's shape so the same failure cannot come back quietly:

* compose names the mirrored GHCR image, at the tag the mirror workflow
  publishes, never an upstream registry;
* the mirror is pinned by digest and checks the upstream signature;
* the store is reachable only on the ``storage`` network, and every service
  that is handed an object-store endpoint is on it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"
MIRROR = REPO_ROOT / ".github" / "workflows" / "mirror-images.yml"


def _services() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def _mirrors() -> dict[str, dict]:
    data = yaml.safe_load(MIRROR.read_text(encoding="utf-8"))
    rows = data["jobs"]["mirror"]["strategy"]["matrix"]["include"]
    return {row["name"]: row for row in rows}


def _networks(service: dict) -> set[str]:
    nets = service.get("networks") or []
    return set(nets) if isinstance(nets, list) else set(nets)


def test_minio_is_gone_from_compose() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert "minio/minio" not in text
    assert "minio" not in _services()


def test_the_store_runs_the_mirrored_image_at_the_mirrored_tag() -> None:
    image = _services()["object-store"]["image"]
    match = re.fullmatch(
        r"ghcr\.io/fidelodok/metaforge-seaweedfs:\$\{METAFORGE_SEAWEEDFS_TAG:-([^}]+)\}", image
    )
    assert match, f"object-store does not name the mirrored image: {image}"
    assert match.group(1) == str(_mirrors()["seaweedfs"]["tag"]), (
        "compose's default tag is not the one mirror-images.yml publishes"
    )


def test_every_mirror_is_pinned_and_checked() -> None:
    for name, row in _mirrors().items():
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", row["digest"]), f"{name} is not pinned"
        assert row["identity"].startswith("^https://github"), f"{name} has no signer identity"
        assert not str(row["tag"]).lower() == "latest", f"{name} mirrors a moving tag"


def test_the_store_is_only_on_the_storage_network() -> None:
    store = _services()["object-store"]
    assert _networks(store) == {"storage"}
    assert "minio" in store["networks"]["storage"]["aliases"]


def test_every_service_with_an_object_store_endpoint_can_reach_it() -> None:
    for name, svc in _services().items():
        env = svc.get("environment") or []
        entries = env if isinstance(env, list) else [f"{k}={v}" for k, v in env.items()]
        if any(str(e).startswith("MINIO_ENDPOINT=") for e in entries):
            assert "storage" in _networks(svc), f"{name} has MINIO_ENDPOINT but no storage network"


def test_the_storage_server_is_not_on_the_network() -> None:
    """Only the S3 gateway binds beyond loopback; the storage server's
    unauthenticated APIs stay inside the container."""
    command = " ".join(_services()["object-store"]["command"])
    server, gateway = command.split("exec weed s3", 1)
    assert "-ip.bind=127.0.0.1" in server
    assert "-filer.disableHttp" in server
    assert "-ip.bind=0.0.0.0" in gateway
    assert "-port.iceberg=0" in gateway and "-port.lance=0" in gateway


class TestTheMirrorIsVerifiedAndSigned:
    """A mirror builds nothing, so the build-time checks in test_image_signing
    do not apply. What must hold instead: the upstream signature is checked
    before copying, the copy keeps the verified digest, and the copy is
    signed as ours by digest (which is what host_update.sh verifies)."""

    def _job(self) -> dict:
        data = yaml.safe_load(MIRROR.read_text(encoding="utf-8"))
        job: dict = data["jobs"]["mirror"]
        return job

    def _step(self, name: str) -> dict:
        for step in self._job()["steps"]:
            if step.get("name") == name:
                return step
        raise AssertionError(f"mirror job has no step {name!r}")

    def test_the_job_can_mint_a_signing_identity(self) -> None:
        assert self._job()["permissions"].get("id-token") == "write"

    def test_the_upstream_signature_is_checked_before_the_copy(self) -> None:
        names = [s.get("name") for s in self._job()["steps"]]
        assert names.index("Verify the upstream signature") < names.index(
            "Copy every platform, byte for byte"
        )
        run = self._step("Verify the upstream signature")["run"]
        assert "cosign verify" in run and "--certificate-oidc-issuer" in run

    def test_the_copy_must_keep_the_verified_digest(self) -> None:
        run = self._step("Copy every platform, byte for byte")["run"]
        assert "matrix.digest" in run and "exit 1" in run

    def test_the_copy_is_signed_by_digest(self) -> None:
        step = self._step("Sign the mirrored image as ours")
        assert "cosign sign" in step["run"]
        assert "@${DIGEST}" in step["run"]
        assert "steps.copy.outputs.digest" in step["env"]["DIGEST"]
