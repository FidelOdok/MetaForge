"""Unit tests for the MinIO KB-storage adapter (MET-476).

The adapter lazy-imports the ``minio`` package, so all tests here pass a
fake client into ``MinIOKBStorage(client=...)`` and never touch the real
SDK. The "no-dep import" guarantee — ``import digital_twin.storage``
works without ``minio`` installed — is covered by a subprocess test.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from digital_twin.storage import (
    DEFAULT_BUCKET_NAME,
    KBPrefix,
    KBStorageError,
    MinIODependencyError,
    MinIOKBStorage,
    MinIOSettings,
)
from digital_twin.storage.kb_storage import _PREFIX_MARKER

# ---------------------------------------------------------------------------
# Fake Minio client (just enough surface to drive the adapter)
# ---------------------------------------------------------------------------


class _FakeStat:
    def __init__(self, size: int) -> None:
        self.size = size


class _FakeObj:
    def __init__(self, name: str, size: int, etag: str | None = None) -> None:
        self.object_name = name
        self.size = size
        self.etag = etag


class _FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.closed = False
        self.released = False

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


class _FakeMinio:
    """Minimal stand-in for ``minio.Minio`` covering the adapter's calls."""

    def __init__(self) -> None:
        self.buckets: set[str] = set()
        self.objects: dict[tuple[str, str], bytes] = {}
        self.versioning: dict[str, bool] = {}
        self.lifecycle: dict[str, object] = {}

    def bucket_exists(self, bucket: str) -> bool:
        return bucket in self.buckets

    def make_bucket(self, bucket: str, location: str | None = None) -> None:
        self.buckets.add(bucket)

    def set_bucket_versioning(self, bucket: str, _config: object) -> None:
        self.versioning[bucket] = True

    def set_bucket_lifecycle(self, bucket: str, config: object) -> None:
        self.lifecycle[bucket] = config

    def stat_object(self, bucket: str, key: str) -> _FakeStat:
        if (bucket, key) not in self.objects:
            raise RuntimeError("not found")
        return _FakeStat(size=len(self.objects[(bucket, key)]))

    def put_object(self, bucket, key, stream, length, content_type=None, metadata=None) -> None:  # noqa: ANN001 — mirrors minio SDK
        self.objects[(bucket, key)] = stream.read()

    def get_object(self, bucket: str, key: str) -> _FakeResponse:
        if (bucket, key) not in self.objects:
            raise RuntimeError("not found")
        return _FakeResponse(self.objects[(bucket, key)])

    def list_objects(self, bucket, prefix, recursive):  # noqa: ANN001 — mirrors minio SDK
        for (b, key), payload in self.objects.items():
            if b == bucket and key.startswith(prefix):
                yield _FakeObj(name=key, size=len(payload), etag="etag-" + key)

    def remove_object(self, bucket: str, key: str) -> None:
        self.objects.pop((bucket, key), None)


def _settings(bucket: str = DEFAULT_BUCKET_NAME) -> MinIOSettings:
    return MinIOSettings(
        endpoint="localhost:9000",
        access_key="k",
        secret_key="s",
        bucket=bucket,
        secure=False,
    )


# ---------------------------------------------------------------------------
# Settings + dependency handling
# ---------------------------------------------------------------------------


def test_settings_from_env_round_trip():
    env = {
        "MINIO_ENDPOINT": "play.min.io:443",
        "MINIO_ACCESS_KEY": "ak",
        "MINIO_SECRET_KEY": "sk",
        "MINIO_BUCKET": "kb",
        "MINIO_SECURE": "false",
        "MINIO_REGION": "us-east-1",
    }
    s = MinIOSettings.from_env(env)
    assert s.endpoint == "play.min.io:443"
    assert s.bucket == "kb"
    assert s.secure is False
    assert s.region == "us-east-1"


def test_settings_from_env_defaults():
    env = {
        "MINIO_ENDPOINT": "minio:9000",
        "MINIO_ACCESS_KEY": "k",
        "MINIO_SECRET_KEY": "s",
    }
    s = MinIOSettings.from_env(env)
    assert s.bucket == DEFAULT_BUCKET_NAME
    assert s.secure is True
    assert s.region is None


def test_settings_from_env_missing_required():
    with pytest.raises(KBStorageError, match="MINIO_ENDPOINT"):
        MinIOSettings.from_env({})


def test_dependency_error_when_minio_absent(monkeypatch):
    # Simulate the no-``minio`` environment by patching the lazy resolver.
    from digital_twin.storage import minio_adapter

    def _boom() -> object:
        raise MinIODependencyError("The 'minio' package is required")

    monkeypatch.setattr(minio_adapter, "_resolve_minio_module", _boom)
    with pytest.raises(MinIODependencyError, match="minio"):
        MinIOKBStorage(_settings())


def test_package_imports_without_minio_installed():
    # ``import digital_twin.storage`` must NOT pull in the optional ``minio``
    # dep. We assert this in a subprocess that blocks the real package via
    # ``sys.modules`` so the result is reliable even when minio happens to
    # be installed in the parent env.
    src = (
        "import sys\n"
        "sys.modules['minio'] = None\n"
        "import digital_twin.storage as s\n"
        "assert s.KBPrefix and s.InMemoryKBStorage and s.MinIOKBStorage\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", src], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# Adapter behaviour against the fake client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_is_idempotent_and_creates_prefix_markers():
    fake = _FakeMinio()
    storage = MinIOKBStorage(_settings(), client=fake)

    await storage.initialize()
    await storage.initialize()  # second call is a no-op

    assert DEFAULT_BUCKET_NAME in fake.buckets
    for prefix in KBPrefix:
        assert (DEFAULT_BUCKET_NAME, f"{prefix.value}/{_PREFIX_MARKER}") in fake.objects


@pytest.mark.asyncio
async def test_put_get_round_trip_with_fake_client():
    fake = _FakeMinio()
    storage = MinIOKBStorage(_settings(), client=fake)
    await storage.initialize()

    key = await storage.put_object(KBPrefix.DATASHEETS, "esp32.pdf", b"payload")
    assert key == "datasheets/esp32.pdf"
    assert await storage.exists(KBPrefix.DATASHEETS, "esp32.pdf")
    assert await storage.get_object(KBPrefix.DATASHEETS, "esp32.pdf") == b"payload"


@pytest.mark.asyncio
async def test_get_missing_raises_kb_storage_error():
    fake = _FakeMinio()
    storage = MinIOKBStorage(_settings(), client=fake)
    await storage.initialize()
    with pytest.raises(KBStorageError, match="not found"):
        await storage.get_object(KBPrefix.DATASHEETS, "missing.pdf")


@pytest.mark.asyncio
async def test_list_excludes_prefix_marker():
    fake = _FakeMinio()
    storage = MinIOKBStorage(_settings(), client=fake)
    await storage.initialize()
    await storage.put_object(KBPrefix.DATASHEETS, "a.pdf", b"a")
    await storage.put_object(KBPrefix.DATASHEETS, "b.pdf", b"bb")

    listings = await storage.list_objects(KBPrefix.DATASHEETS)
    keys = [item.key for item in listings]
    assert keys == ["datasheets/a.pdf", "datasheets/b.pdf"]


@pytest.mark.asyncio
async def test_delete_returns_existed_flag():
    fake = _FakeMinio()
    storage = MinIOKBStorage(_settings(), client=fake)
    await storage.initialize()
    await storage.put_object(KBPrefix.BOM_SPECS, "bom.yaml", b"x")

    assert await storage.delete_object(KBPrefix.BOM_SPECS, "bom.yaml") is True
    assert await storage.delete_object(KBPrefix.BOM_SPECS, "bom.yaml") is False


# ---------------------------------------------------------------------------
# Bucket lifecycle policy (MET-476)
# ---------------------------------------------------------------------------


class _SentinelConfig:
    """Stand-in lifecycle config so tests don't need the real minio types."""


@pytest.mark.asyncio
async def test_apply_kb_lifecycle_calls_set_bucket_lifecycle(monkeypatch):
    from digital_twin.storage import minio_adapter

    sentinel = _SentinelConfig()
    captured: dict[str, object] = {}

    def fake_build(*, archive_after_days: int, storage_class: str) -> object:
        captured["archive_after_days"] = archive_after_days
        captured["storage_class"] = storage_class
        return sentinel

    monkeypatch.setattr(minio_adapter, "_build_kb_lifecycle_config", fake_build)

    fake = _FakeMinio()
    storage = MinIOKBStorage(_settings(), client=fake)
    await storage.apply_kb_lifecycle()

    # Defaults flowed through to the builder.
    assert captured["archive_after_days"] == minio_adapter.DEFAULT_ARCHIVE_AFTER_DAYS
    assert captured["storage_class"] == minio_adapter.DEFAULT_ARCHIVE_STORAGE_CLASS
    # The fake captured the SDK call with the sentinel config and bucket name.
    assert fake.lifecycle[DEFAULT_BUCKET_NAME] is sentinel


@pytest.mark.asyncio
async def test_apply_kb_lifecycle_custom_archive_settings(monkeypatch):
    from digital_twin.storage import minio_adapter

    captured: dict[str, object] = {}

    def fake_build(*, archive_after_days: int, storage_class: str) -> object:
        captured["archive_after_days"] = archive_after_days
        captured["storage_class"] = storage_class
        return _SentinelConfig()

    monkeypatch.setattr(minio_adapter, "_build_kb_lifecycle_config", fake_build)

    storage = MinIOKBStorage(_settings(), client=_FakeMinio())
    await storage.apply_kb_lifecycle(archive_after_days=30, storage_class="STANDARD_IA")

    assert captured == {"archive_after_days": 30, "storage_class": "STANDARD_IA"}


def test_build_kb_lifecycle_config_rejects_invalid_inputs():
    from digital_twin.storage.minio_adapter import _build_kb_lifecycle_config

    with pytest.raises(ValueError, match="archive_after_days"):
        _build_kb_lifecycle_config(archive_after_days=0)
    with pytest.raises(ValueError, match="storage_class"):
        _build_kb_lifecycle_config(archive_after_days=365, storage_class="")


def test_build_kb_lifecycle_config_real_minio_shape():
    """Structural check against the real minio types — skip when not installed."""
    pytest.importorskip("minio")
    from digital_twin.storage.minio_adapter import (
        DEFAULT_ARCHIVE_AFTER_DAYS,
        DEFAULT_ARCHIVE_STORAGE_CLASS,
        LIFECYCLE_RULE_ID,
        _build_kb_lifecycle_config,
    )

    config = _build_kb_lifecycle_config()
    rules = list(config.rules)
    assert len(rules) == 1
    rule = rules[0]
    assert rule.rule_id == LIFECYCLE_RULE_ID
    assert rule.status == "Enabled"
    assert rule.transition is not None
    assert rule.transition.days == DEFAULT_ARCHIVE_AFTER_DAYS
    assert rule.transition.storage_class == DEFAULT_ARCHIVE_STORAGE_CLASS
