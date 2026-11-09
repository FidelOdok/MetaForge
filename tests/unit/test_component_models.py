"""Model resolution and cache integration through the existing component catalog."""

import asyncio
import hashlib
from pathlib import Path

import pytest

from tool_registry.asset_cache import AssetIntegrityError, VerifiedAssetCache
from tool_registry.tools.kicad.config import KicadConfig
from tool_registry.tools.kicad.library import describe, search
from tool_registry.tools.library.model_resolver import resolve
from twin_core.library.catalog import CatalogError, SqliteCatalog

DATA = b"<mujoco/>"
DIGEST = hashlib.sha256(DATA).hexdigest()


def model():
    return {
        "model_id": "motor",
        "version": "1.0.0",
        "kind": "physics",
        "engine": "mujoco",
        "engine_versions": ["3.0"],
        "entrypoint": "model.xml",
        "usage": "Load model.xml.",
        "limitations": ["No thermal model"],
        "artifacts": [
            {
                "path": "model.xml",
                "url": "https://huggingface.co/example/model.xml",
                "sha256": DIGEST,
                "size": len(DATA),
                "licence": "MIT",
                "source_revision": "a" * 40,
            }
        ],
    }


class Fetcher:
    def __init__(self):
        self.calls = 0

    async def iter_bytes(self, url):
        self.calls += 1
        await asyncio.sleep(0)
        yield DATA


class Service:
    def __init__(self, catalog):
        self.catalog = catalog

    async def get_entry(self, kind, ref):
        return {"entry": self.catalog.get(ref, kind).as_dict()}


def catalog():
    store = SqliteCatalog()
    store.register(
        "component",
        {"name": "motor", "family": "motor", "origin": "purchased", "models": [model()]},
    )
    return store


@pytest.mark.asyncio
async def test_resolve_fetch_replay_and_withdrawal(tmp_path):
    store = catalog()
    service = Service(store)
    fetcher = Fetcher()
    args = {
        "ref": "motor",
        "model_id": "motor",
        "version": "1.0.0",
        "engine": "mujoco",
        "engine_version": "3.0",
        "fetch": True,
    }
    first = await resolve(service, args, workspace=tmp_path, fetcher=fetcher)
    assert first["materialized"] and fetcher.calls == 1
    assert Path(first["entrypoints"][0]["entrypoint"]).read_bytes() == DATA
    second = await resolve(service, {**args, "offline": True}, workspace=tmp_path, fetcher=fetcher)
    assert second["lock_sha256"] == first["lock_sha256"] and fetcher.calls == 1
    assert Path(second["folder"], "model.lock.json").is_file()
    withdrawn = {**model(), "status": "withdrawn", "status_reason": "incorrect torque"}
    store.update(store.get("motor", "component").id, {"models": [withdrawn]})
    with pytest.raises(ValueError, match="withdrawn"):
        await resolve(service, args, workspace=tmp_path, fetcher=fetcher)
    assert fetcher.calls == 1


@pytest.mark.asyncio
async def test_engine_conflict_and_dependency_cycle_prevent_network(tmp_path):
    store = catalog()
    args = {
        "ref": "motor",
        "model_id": "motor",
        "version": "1.0.0",
        "engine": "renode",
        "fetch": True,
    }
    fetcher = Fetcher()
    with pytest.raises(ValueError, match="engine"):
        await resolve(Service(store), args, workspace=tmp_path, fetcher=fetcher)
    cyclic = model()
    cyclic["dependencies"] = [{"component": "motor", "model_id": "motor", "version": "1.0.0"}]
    fresh = SqliteCatalog()
    fresh.register(
        "component", {"name": "motor", "family": "motor", "origin": "purchased", "models": [cyclic]}
    )
    with pytest.raises(ValueError, match="cycle"):
        await resolve(
            Service(fresh),
            {**args, "engine": "mujoco", "engine_version": "3.0"},
            workspace=tmp_path,
            fetcher=fetcher,
        )
    assert fetcher.calls == 0


def test_model_version_content_is_immutable_and_old_components_still_load():
    store = catalog()
    entry = store.get("motor", "component")
    changed = {**model(), "usage": "changed behavior"}
    with pytest.raises(CatalogError, match="immutable"):
        store.update(entry.id, {"models": [changed]})
    assert store.get(entry.id).models[0]["usage"] == model()["usage"]
    with pytest.raises(CatalogError, match="withdraw"):
        store.update(entry.id, {"models": []})
    legacy = store.register(
        "component", {"name": "legacy", "family": "motor", "origin": "purchased"}
    )
    assert store.get(legacy.id).models == []


@pytest.mark.asyncio
async def test_cache_coordinates_concurrent_consumers_and_detects_corruption(tmp_path):
    cache = VerifiedAssetCache(tmp_path / "cache")
    fetcher = Fetcher()

    async def request(i):
        return await cache.fetch_to(
            url="unused",
            sha256=DIGEST,
            size=len(DATA),
            target=tmp_path / f"job{i}.xml",
            fetcher=fetcher,
        )

    hits = await asyncio.gather(*(request(i) for i in range(5)))
    assert fetcher.calls == 1 and sum(hits) == 4
    (tmp_path / "cache" / DIGEST[:2] / DIGEST).write_bytes(b"corrupt")
    with pytest.raises(AssetIntegrityError):
        await cache.fetch_to(
            url="unused",
            sha256=DIGEST,
            size=len(DATA),
            target=tmp_path / "offline.xml",
            fetcher=fetcher,
            offline=True,
        )


def test_kicad_installed_search_and_inherited_component_details():
    config = KicadConfig(symbol_dir="tests/unit/fixtures/kicad/symbols")
    result = search(config, "Device:R")
    assert any(r["symbol_id"] == "Device:R" for r in result["results"])
    description = describe(config, "Device:R")
    assert description["pins"] and len(description["source"]["sha256"]) == 64
    with pytest.raises(ValueError):
        describe(config, "../../escape:R")
