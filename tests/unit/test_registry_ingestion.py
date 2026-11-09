"""Regression cases from the registry audit, using no external services."""

import hashlib

import httpx
import pytest

from tool_registry.tools.simready.importer import asset_identity, import_identity, library_name
from tool_registry.tools.simready.package import (
    HttpFetcher,
    Manifest,
    PackageError,
    PackageRef,
    bounded_get,
    download,
)

REF = PackageRef("vendor-a", "models", "a" * 40, "motor")


def manifest(size: int, data: bytes) -> Manifest:
    item = {
        "relative_path": "motor.usda",
        "size": size,
        "hash": {"sha256": hashlib.sha256(data).hexdigest()},
    }
    return Manifest(REF, "motor", "MIT", "b" * 64, [item], "motor.usda")


class StreamingFetcher:
    def __init__(self, data):
        self.data = data
        self.calls = 0

    async def iter_bytes(self, url):
        self.calls += 1
        for start in range(0, len(self.data), 4):
            yield self.data[start : start + 4]


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [1, -1, True, "1", None])
async def test_actual_budget_cannot_be_bypassed(tmp_path, declared):
    m = manifest(declared, b"x" * 1024)
    with pytest.raises(PackageError):
        await download(m, m.items, tmp_path, StreamingFetcher(b"x" * 1024), max_bytes=8)
    assert not (tmp_path / "motor.usda").exists()
    assert not list(tmp_path.glob(".download-*"))


@pytest.mark.asyncio
async def test_failed_transfer_keeps_previous_file(tmp_path):
    target = tmp_path / "motor.usda"
    target.write_bytes(b"previous")
    m = manifest(10, b"x" * 10)
    with pytest.raises(PackageError, match="size"):
        await download(m, m.items, tmp_path, StreamingFetcher(b"x"), max_bytes=100)
    assert target.read_bytes() == b"previous"


@pytest.mark.asyncio
async def test_metadata_has_an_actual_byte_cap():
    with pytest.raises(PackageError, match="byte limit"):
        await bounded_get(StreamingFetcher(b"x" * 20), "url", max_bytes=8)


def test_namespace_and_sanitized_name_collisions_are_distinct():
    other = PackageRef("vendor-b", "models", "a" * 40, "motor")
    punctuation = PackageRef("vendor-a", "models", "a" * 40, "Motor!")
    assert library_name(REF) == library_name(other) == library_name(punctuation)
    assert len({import_identity(r) for r in (REF, other, punctuation)}) == 3
    assert import_identity(REF) != import_identity(REF, True)


@pytest.mark.asyncio
async def test_http_fetcher_streams_and_limits_before_buffering(monkeypatch):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 100))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))
    with pytest.raises(PackageError):
        await bounded_get(HttpFetcher(), "https://huggingface.co/example", max_bytes=4)


def test_component_identity_survives_a_new_upstream_revision():
    newer = PackageRef(REF.namespace, REF.dataset, "b" * 40, REF.asset)
    assert asset_identity(newer) == asset_identity(REF)
    assert import_identity(newer) != import_identity(REF)
