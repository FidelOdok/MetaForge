"""The design-flow worker is configured like the gateway it stands in for (FORGE-475).

A phase runs the gateway's chat harness in another process. Live, that process
had only ``OPEN_ROUTER_API_KEY``: every phase resolved the in-code default
provider, had no key for it, and ran with no MCP tools. These are structural
assertions on docker-compose.yml; they do not need Docker.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"

#: What the worker must carry exactly as the gateway does, so the two resolve
#: the same provider/model from the same .env.
SHARED_ENV = (
    "METAFORGE_LLM_PROVIDER",
    "METAFORGE_LLM_MODEL",
    "METAFORGE_LLM_BASE_URL",
    "METAFORGE_LLM_API_KEY",
    "METAFORGE_CHAT_SKILLS",
    "METAFORGE_CHAT_MAX_STEPS",
    "METAFORGE_CHAT_WALL_CLOCK_SECONDS",
    "METAFORGE_CHAT_MAX_COST_USD",
    "METAFORGE_ROTATION_STRATEGY",
    "OPEN_ROUTER_API_KEY",
)


def _services() -> dict:
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return data["services"]


def _env(service: dict) -> dict[str, str]:
    env = service.get("environment", [])
    assert isinstance(env, list), "environment is expected in list form"
    return dict(entry.split("=", 1) for entry in env)


def test_worker_reads_the_same_provider_config_as_the_gateway() -> None:
    services = _services()
    gateway, worker = _env(services["gateway"]), _env(services["design-flow-worker"])
    for key in SHARED_ENV:
        assert key in worker, f"design-flow-worker is missing {key}"
        assert worker[key] == gateway[key], f"{key} differs from the gateway's"


def test_worker_shares_the_durable_harness_selection() -> None:
    """``forge auth use``/``login`` state lives in these volumes, not in env."""
    services = _services()
    gateway_vols = set(services["gateway"].get("volumes", []))
    worker_vols = set(services["design-flow-worker"].get("volumes", []))
    for mount in ("metaforge-home:/root/.metaforge", "codex-home:/root/.codex"):
        assert mount in gateway_vols
        assert mount in worker_vols, f"design-flow-worker does not mount {mount}"


def test_worker_is_pointed_at_the_mcp_sidecar() -> None:
    worker = _env(_services()["design-flow-worker"])
    assert worker.get("METAFORGE_MCP_URL") == "${METAFORGE_MCP_URL:-http://mcp-http:8765/mcp}"
    # The sidecar's key, when it is guarded, is the client key here.
    assert worker.get("METAFORGE_MCP_CLIENT_KEY") == "${METAFORGE_MCP_API_KEY:-}"


def test_service_key_goes_to_the_worker_and_the_sidecar_only() -> None:
    """FORGE-487: the secret is shared by two services and has no default."""
    base = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    override = yaml.safe_load(
        (REPO_ROOT / "docker-compose.override.yml").read_text(encoding="utf-8")
    )["services"]
    expected = "METAFORGE_MCP_SERVICE_KEY=${METAFORGE_MCP_SERVICE_KEY:-}"

    assert expected in base["design-flow-worker"]["environment"]
    assert expected in override["mcp-http"]["environment"]

    holders = [
        name
        for source in (base, override)
        for name, service in source.items()
        if any(
            "METAFORGE_MCP_SERVICE_KEY" in entry
            for entry in (service.get("environment") or [])
            if isinstance(entry, str)
        )
    ]
    assert sorted(holders) == ["design-flow-worker", "mcp-http"]


def test_every_temporal_client_sets_temporal_host() -> None:
    """FORGE-488: unset, the client dials localhost:7233 inside its own container."""
    base = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    override = yaml.safe_load(
        (REPO_ROOT / "docker-compose.override.yml").read_text(encoding="utf-8")
    )["services"]
    for name in ("gateway", "temporal-worker", "design-flow-worker", "mcp-http"):
        entries = [
            entry
            for source in (base, override)
            for entry in (source.get(name, {}).get("environment") or [])
            if isinstance(entry, str)
        ]
        assert "TEMPORAL_HOST=temporal:7233" in entries, f"{name} does not set TEMPORAL_HOST"


OVERRIDE = REPO_ROOT / "docker-compose.override.yml"


def _source_mounts(service: dict) -> set[str]:
    """Host source paths (``./x``) a service mounts, ignoring named volumes."""
    out = set()
    for entry in service.get("volumes", []):
        host = str(entry).split(":", 1)[0]
        if host.startswith("./"):
            out.add(host)
    return out


def test_dev_worker_mounts_the_same_source_as_the_gateway() -> None:
    """FORGE-493: in dev a worker fix applies on restart, not on an image pull.

    The worker runs the gateway's own modules (harness, runs, design flows), so
    any source dir the dev gateway mounts and the worker does not would run
    stale image code next to fresh gateway code.
    """
    data = yaml.safe_load(OVERRIDE.read_text(encoding="utf-8"))["services"]
    assert "design-flow-worker" in data, "dev override has no design-flow-worker block"
    missing = _source_mounts(data["gateway"]) - _source_mounts(data["design-flow-worker"])
    assert not missing, f"design-flow-worker does not mount {sorted(missing)}"
