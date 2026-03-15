"""Bootstrap tool adapters at application startup.

Discovers, instantiates, and registers all available tool adapters into
a ToolRegistry. Called by the API Gateway during lifespan initialization.

Adapter registration is config-driven via environment variables:
- METAFORGE_ADAPTERS: comma-separated list of adapter IDs to enable
  (default: all known adapters)
- METAFORGE_ADAPTER_{ID}_ENABLED: per-adapter toggle
  (e.g., METAFORGE_ADAPTER_CADQUERY_ENABLED=false)
"""

from __future__ import annotations

import os
from typing import Any

import structlog

from observability.tracing import get_tracer
from tool_registry.registry import ToolRegistry

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.bootstrap")

# Known adapters and their factory functions (import path, class, config class)
_ADAPTER_REGISTRY: dict[str, dict[str, str]] = {
    "cadquery": {
        "module": "tool_registry.tools.cadquery.adapter",
        "class": "CadqueryServer",
        "config_module": "tool_registry.tools.cadquery.config",
        "config_class": "CadqueryConfig",
    },
    "freecad": {
        "module": "tool_registry.tools.freecad.adapter",
        "class": "FreecadServer",
        "config_module": "tool_registry.tools.freecad.config",
        "config_class": "FreecadConfig",
    },
    "calculix": {
        "module": "tool_registry.tools.calculix.adapter",
        "class": "CalculixServer",
        "config_module": "tool_registry.tools.calculix.config",
        "config_class": "CalculixConfig",
    },
}


def _is_adapter_enabled(adapter_id: str) -> bool:
    """Check if an adapter is enabled via environment variables."""
    # Per-adapter toggle: METAFORGE_ADAPTER_CADQUERY_ENABLED=false
    env_key = f"METAFORGE_ADAPTER_{adapter_id.upper()}_ENABLED"
    env_val = os.environ.get(env_key, "").lower()
    if env_val == "false":
        return False
    if env_val == "true":
        return True

    # Global allowlist: METAFORGE_ADAPTERS=cadquery,calculix
    adapters_env = os.environ.get("METAFORGE_ADAPTERS", "")
    if adapters_env:
        allowed = {a.strip().lower() for a in adapters_env.split(",")}
        return adapter_id.lower() in allowed

    # Default: enabled
    return True


def _import_class(module_path: str, class_name: str) -> type | None:
    """Dynamically import a class from a module path."""
    try:
        import importlib

        module = importlib.import_module(module_path)
        return getattr(module, class_name)
    except (ImportError, AttributeError) as exc:
        logger.debug(
            "Could not import adapter class",
            module=module_path,
            class_name=class_name,
            error=str(exc),
        )
        return None


def _create_adapter(adapter_id: str, spec: dict[str, str]) -> Any | None:
    """Instantiate an adapter server with its default config.

    Returns the McpToolServer instance, or None if import fails.
    """
    server_cls = _import_class(spec["module"], spec["class"])
    if server_cls is None:
        return None

    config_cls = _import_class(spec["config_module"], spec["config_class"])
    if config_cls is not None:
        config = config_cls()
        return server_cls(config=config)

    return server_cls()


async def bootstrap_tool_registry(
    registry: ToolRegistry | None = None,
    adapter_ids: list[str] | None = None,
) -> ToolRegistry:
    """Bootstrap all enabled tool adapters into a ToolRegistry.

    Args:
        registry: Existing registry to populate. Creates a new one if None.
        adapter_ids: Explicit list of adapter IDs to register. If None,
            registers all known adapters that are enabled.

    Returns:
        The populated ToolRegistry.
    """
    with tracer.start_as_current_span("bootstrap_tool_registry") as span:
        if registry is None:
            registry = ToolRegistry()

        ids_to_register = adapter_ids or list(_ADAPTER_REGISTRY.keys())
        registered: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []

        for adapter_id in ids_to_register:
            spec = _ADAPTER_REGISTRY.get(adapter_id)
            if spec is None:
                logger.warning("Unknown adapter ID", adapter_id=adapter_id)
                failed.append(adapter_id)
                continue

            if not _is_adapter_enabled(adapter_id):
                logger.info("Adapter disabled via config", adapter_id=adapter_id)
                skipped.append(adapter_id)
                continue

            server = _create_adapter(adapter_id, spec)
            if server is None:
                logger.warning(
                    "Adapter import failed (module not available)",
                    adapter_id=adapter_id,
                )
                skipped.append(adapter_id)
                continue

            try:
                await registry.register_adapter(server)
                registered.append(adapter_id)
            except Exception as exc:
                logger.error(
                    "Adapter registration failed",
                    adapter_id=adapter_id,
                    error=str(exc),
                )
                span.record_exception(exc)
                failed.append(adapter_id)

        span.set_attribute("adapters.registered", len(registered))
        span.set_attribute("adapters.skipped", len(skipped))
        span.set_attribute("adapters.failed", len(failed))

        logger.info(
            "Tool registry bootstrap complete",
            registered=registered,
            skipped=skipped,
            failed=failed,
            total_tools=len(registry.list_tools()),
        )

        return registry
