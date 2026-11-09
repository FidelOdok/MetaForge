"""Resolve versioned component representations through the existing library service."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.asset_cache import VerifiedAssetCache
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.tools.simready.package import HttpFetcher
from twin_core.library.licences import is_accepted
from twin_core.library.model_contracts import ModelRepresentation

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.library.model_resolver")


async def resolve(
    service: Any, arguments: dict[str, Any], *, workspace: Path, fetcher: Any = None
) -> dict[str, Any]:
    """Resolve metadata first. All constraints must pass before any download."""
    fetcher = fetcher or HttpFetcher()
    nodes: dict[tuple[str, str, str], ModelRepresentation] = {}
    visiting: set[tuple[str, str, str]] = set()
    warnings: list[str] = []
    engine = str(arguments.get("engine") or "")
    engine_version = arguments.get("engine_version")

    async def visit(component: str, model_id: str, version: str) -> None:
        key = (component, model_id, version)
        if key in visiting:
            raise ValueError("model dependency cycle")
        if key in nodes:
            return
        if len(nodes) + len(visiting) >= 100:
            raise ValueError("model dependency graph exceeds 100 entries")
        response = await service.get_entry("component", component)
        raw = response.get("entry", {}).get("models", [])
        found = [m for m in raw if m["model_id"] == model_id and m["version"] == version]
        if len(found) != 1:
            raise ValueError(f"model {model_id}@{version} is not registered on {component}")
        model = ModelRepresentation.model_validate(found[0])
        if model.status == "withdrawn":
            raise ValueError(f"model withdrawn: {model.status_reason}")
        if model.status == "deprecated":
            warnings.append(f"{model_id}: deprecated: {model.status_reason}")
        if model.engine != engine:
            raise ValueError(f"model requires engine {model.engine}, requested {engine}")
        if model.engine_versions and engine_version not in model.engine_versions:
            raise ValueError("an explicitly supported engine version is required")
        for asset in model.artifacts:
            if not is_accepted(asset.licence):
                raise ValueError(f"asset licence requires review: {asset.licence}")
        visiting.add(key)
        for dep in model.dependencies:
            await visit(dep.component, dep.model_id, dep.version)
        visiting.remove(key)
        nodes[key] = model

    await visit(str(arguments["ref"]), str(arguments["model_id"]), str(arguments["version"]))
    entries = [
        {"component": key[0], **model.model_dump(mode="json")} for key, model in nodes.items()
    ]
    lock = {
        "schema_version": 1,
        "engine": engine,
        "engine_version": engine_version,
        "models": entries,
    }
    encoded = json.dumps(lock, sort_keys=True, separators=(",", ":")).encode()
    result: dict[str, Any] = {
        "lockfile": lock,
        "lock_sha256": hashlib.sha256(encoded).hexdigest(),
        "warnings": warnings,
        "limitations": [v for m in nodes.values() for v in m.limitations],
        "materialized": False,
    }
    total = sum(a.size for m in nodes.values() for a in m.artifacts)
    if total > 200 * 1024 * 1024:
        raise ValueError("model graph exceeds the 200 MiB materialization limit")
    if arguments.get("fetch", False):
        root = workspace / "library-models"
        root.mkdir(parents=True, exist_ok=True)
        import shutil

        job = Path(tempfile.mkdtemp(prefix="resolved-", dir=root))
        paths = []
        cache = VerifiedAssetCache(workspace / ".asset-cache")
        try:
            for key, model in nodes.items():
                prefix = hashlib.sha256(json.dumps(key).encode()).hexdigest()
                for asset in model.artifacts:
                    target = job / prefix / asset.path
                    await cache.fetch_to(
                        url=asset.url,
                        sha256=asset.sha256,
                        size=asset.size,
                        target=target,
                        fetcher=fetcher,
                        offline=bool(arguments.get("offline", False)),
                    )
                paths.append(
                    {
                        "component": key[0],
                        "model_id": key[1],
                        "entrypoint": str(job / prefix / model.entrypoint),
                    }
                )
            (job / "model.lock.json").write_bytes(encoded)
        except BaseException:
            shutil.rmtree(job, ignore_errors=True)
            raise
        result.update(materialized=True, folder=str(job), entrypoints=paths)
    logger.info(
        "component_models_resolved",
        models=len(nodes),
        bytes=total,
        materialized=result["materialized"],
        lock_sha256=result["lock_sha256"],
    )
    return result


def register_resolver(register: Any, service: Any) -> None:
    async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("library.resolve_component_model"):
            return await resolve(
                service, arguments, workspace=Path(os.getenv("ADAPTER_WORKSPACE_DIR", "/workspace"))
            )

    register(
        manifest=ToolManifest(
            tool_id="library.resolve_component_model",
            adapter_id="library",
            name="Resolve a component model",
            description=(
                "Read model usage and limitations, resolve exact dependencies and engine "
                "compatibility; optionally fetch verified assets into an isolated job folder. "
                "Never executes the model."
            ),
            capability="model_resolution",
            phase=2,
            chat_visible=False,
            chat_discoverable=True,
            input_schema={
                "type": "object",
                "properties": {
                    "ref": {"type": "string"},
                    "model_id": {"type": "string"},
                    "version": {"type": "string"},
                    "engine": {"type": "string"},
                    "engine_version": {"type": "string"},
                    "fetch": {"type": "boolean", "default": False},
                    "offline": {"type": "boolean", "default": False},
                },
                "required": ["ref", "model_id", "version", "engine"],
            },
            output_schema={"type": "object"},
            resource_limits=ResourceLimits(max_memory_mb=256, max_cpu_seconds=300, max_disk_mb=256),
        ),
        handler=handler,
    )
