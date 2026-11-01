"""FORGE-594: the gateway sets up the KB bucket, with versioning, at start-up."""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

import pytest

from api_gateway.knowledge.storage_init import ensure_kb_storage
from digital_twin.storage import KBStorageError, MinIOKBStorage, MinIOSettings, minio_adapter

_ENV = {"MINIO_ENDPOINT": "object-store:9000", "MINIO_ACCESS_KEY": "k", "MINIO_SECRET_KEY": "s"}


async def _no_sleep(_: float) -> None:
    return None


class _Storage:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0
        self.bucket = "metaforge-kb"

    async def initialize(self) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("object store not up yet")


def _run(env: dict[str, str], storage: _Storage, attempts: int = 3) -> str:
    return asyncio.run(
        ensure_kb_storage(
            env, attempts=attempts, storage_factory=lambda _env: storage, sleep=_no_sleep
        )
    )


def _counted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    outcomes: list[str] = []

    class _Collector:
        def record_kb_storage_init(self, outcome: str) -> None:
            outcomes.append(outcome)

    import observability.metrics as metrics

    monkeypatch.setattr(metrics, "collector_for", lambda _component: _Collector())
    return outcomes


def test_no_object_store_configured_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes = _counted(monkeypatch)
    storage = _Storage(failures=0)
    assert _run({}, storage) == "skipped"
    assert storage.calls == 0
    assert outcomes == ["skipped"]


def test_a_store_that_comes_up_late_is_waited_for(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes = _counted(monkeypatch)
    storage = _Storage(failures=2)
    assert _run(_ENV, storage) == "ready"
    assert storage.calls == 3
    assert outcomes == ["ready"]


def test_a_store_that_never_comes_up_is_counted_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes = _counted(monkeypatch)
    storage = _Storage(failures=99)
    assert _run(_ENV, storage) == "failed"
    assert storage.calls == 3
    assert outcomes == ["failed"]


# The adapter: versioning that does not take is an error now, not a warning.


class _Versioning:
    def __init__(self, status: str) -> None:
        self.status = status


class _Client:
    def __init__(self, *, refuse: bool = False, reads: str = "Enabled") -> None:
        self.buckets: set[str] = set()
        self.objects: set[str] = set()
        self.refuse = refuse
        self.reads = reads
        self.set_calls = 0

    def bucket_exists(self, bucket: str) -> bool:
        return bucket in self.buckets

    def make_bucket(self, bucket: str, location: str | None = None) -> None:
        self.buckets.add(bucket)

    def set_bucket_versioning(self, bucket: str, config: Any) -> None:
        self.set_calls += 1
        if self.refuse:
            raise RuntimeError("NotImplemented")

    def get_bucket_versioning(self, bucket: str) -> _Versioning:
        return _Versioning(self.reads)

    def stat_object(self, bucket: str, key: str) -> None:
        if key not in self.objects:
            raise RuntimeError("missing")

    def put_object(self, bucket, key, stream, length, content_type=None, metadata=None) -> None:  # noqa: ANN001
        self.objects.add(key)


@pytest.fixture
def _fake_minio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Just enough of the ``minio`` package for the versioning call."""
    pkg = types.ModuleType("minio")
    common = types.ModuleType("minio.commonconfig")
    common.ENABLED = "Enabled"  # type: ignore[attr-defined]
    versioning = types.ModuleType("minio.versioningconfig")
    versioning.VersioningConfig = lambda status: status  # type: ignore[attr-defined]
    pkg.commonconfig = common  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "minio", pkg)
    monkeypatch.setitem(sys.modules, "minio.commonconfig", common)
    monkeypatch.setitem(sys.modules, "minio.versioningconfig", versioning)
    monkeypatch.setattr(minio_adapter, "_resolve_minio_module", lambda: pkg)


def _storage(client: _Client) -> MinIOKBStorage:
    return MinIOKBStorage(MinIOSettings.from_env(_ENV), client=client)


@pytest.mark.usefixtures("_fake_minio")
def test_initialize_creates_the_bucket_and_confirms_versioning() -> None:
    client = _Client()
    asyncio.run(_storage(client).initialize())
    assert "metaforge-kb" in client.buckets
    assert client.set_calls == 1


@pytest.mark.usefixtures("_fake_minio")
def test_a_refused_versioning_call_fails_initialize() -> None:
    with pytest.raises(KBStorageError, match="could not enable versioning"):
        asyncio.run(_storage(_Client(refuse=True)).initialize())


@pytest.mark.usefixtures("_fake_minio")
def test_versioning_that_does_not_read_back_enabled_fails_initialize() -> None:
    with pytest.raises(KBStorageError, match="versioning reads 'Suspended'"):
        asyncio.run(_storage(_Client(reads="Suspended")).initialize())
