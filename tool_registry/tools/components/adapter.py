"""Component catalog MCP tool adapter — parametric + intent search (MET-436).

Exposes:

* ``component.search_parametric`` — structured range/comparison query
  against the typed component catalog (mode 1). Use when the category and
  spec bounds are already known.
* ``component.search_intent`` — free-text intent translated into
  per-category spec bounds, searched via the parametric catalog with a
  fuzzy-knowledge-search fallback per category, and split into
  ``buy_complete`` (COTS assemblies) vs. ``build_from_parts`` (discrete
  components) — never merged (mode 3).

Layer note: ``tool_registry/CLAUDE.md`` normally bars imports from
``digital_twin``. Importing ``digital_twin.catalog``'s query types and
``digital_twin.knowledge``'s ``KnowledgeService``/intent-search pure-logic
functions is the same documented exception
``tool_registry/tools/knowledge/adapter.py`` already uses for the published
L1 knowledge contract — this adapter is the one place a live
``ComponentCatalogStore``, a live ``KnowledgeService``, and a live LLM get
wired together, per the MET-436 plan's architecture (the two
``digital_twin`` sub-packages never import each other; composition happens
here).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import structlog

from digital_twin.catalog.query import CatalogQuery, ComponentCatalogRow, ComponentFilter
from digital_twin.catalog.store import ComponentCatalogStore
from digital_twin.knowledge.intent_search import search_intent
from digital_twin.knowledge.intent_search import to_dict as intent_to_dict
from digital_twin.knowledge.intent_translator import IntentLLM
from digital_twin.knowledge.service import KnowledgeService
from digital_twin.knowledge.subsystem_templates import SubsystemTemplate
from observability.tracing import get_tracer
from tool_registry.mcp_server.handlers import ResourceLimits, ToolManifest
from tool_registry.mcp_server.server import McpToolServer

logger = structlog.get_logger(__name__)
tracer = get_tracer("tool_registry.tools.components.adapter")

# Mirrors digital_twin.catalog.query.CatalogFilterOp's allowed values —
# re-declared locally (not imported) so the manifest schema enum is stable
# even if that module is unavailable at import time, matching the
# knowledge adapter's own _ALLOWED_OPS_FOR_SCHEMA convention.
_ALLOWED_FILTER_OPS: tuple[str, ...] = ("==", "!=", ">=", "<=", ">", "<", "in", "not_in")

_SEARCH_LIMITS = ResourceLimits(max_memory_mb=512, max_cpu_seconds=30)
_INTENT_LIMITS = ResourceLimits(max_memory_mb=512, max_cpu_seconds=60)


class ComponentServer(McpToolServer):
    """MCP wrapper over the parametric component catalog + intent search."""

    def __init__(
        self,
        *,
        search_store: ComponentCatalogStore,
        knowledge_service: KnowledgeService,
        llm: IntentLLM,
        subsystem_templates: Mapping[str, SubsystemTemplate] | None = None,
        known_categories: Sequence[str] | None = None,
    ) -> None:
        super().__init__(adapter_id="component", version="0.1.0")
        self._store = search_store
        self._knowledge_service = knowledge_service
        self._llm = llm
        self._subsystem_templates = subsystem_templates
        self._known_categories = known_categories
        self._register_tools()

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        self.register_tool(
            manifest=ToolManifest(
                tool_id="component.search_parametric",
                adapter_id="component",
                name="Component Parametric Search",
                description=(
                    "Range/comparison query against the typed component catalog "
                    "(MET-436) — e.g. category='buck_converter' with filters "
                    "v_out==5, efficiency>0.9. Fails loudly on an unknown field "
                    "name rather than returning an empty result, unlike "
                    "knowledge.search's filter contract — a typo'd spec name is "
                    "a worse silent failure here than in fuzzy search. Use "
                    "component.search_intent instead when you have a free-text "
                    "goal rather than known category + spec bounds."
                ),
                capability="component_search_parametric",
                input_schema={
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "description": "Catalog category name, e.g. 'buck_converter'.",
                        },
                        "filters": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "property": {"type": "string"},
                                    "op": {"type": "string", "enum": list(_ALLOWED_FILTER_OPS)},
                                    "value": {},
                                },
                                "required": ["property", "op", "value"],
                            },
                            "description": (
                                "List of {property, op, value} range/comparison filters."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "default": 25,
                        },
                    },
                    "required": ["category"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "rows": {"type": "array"},
                        "query_time_ms": {"type": "number"},
                    },
                },
                phase=2,
                resource_limits=_SEARCH_LIMITS,
            ),
            handler=self.handle_search_parametric,
        )

        self.register_tool(
            manifest=ToolManifest(
                tool_id="component.search_intent",
                adapter_id="component",
                name="Component Intent Search",
                description=(
                    "Translate a free-text component need (e.g. 'I need a "
                    "flight controller for a 250mm quad') into category "
                    "candidates and spec bounds, search each via the "
                    "parametric catalog with a fuzzy-knowledge-search "
                    "fallback, and return results split into buy_complete "
                    "(COTS assemblies) vs. build_from_parts (discrete "
                    "components) — never merged (MET-436)."
                ),
                capability="component_search_intent",
                input_schema={
                    "type": "object",
                    "properties": {
                        "intent_text": {
                            "type": "string",
                            "description": "Free-text description of the component need.",
                        },
                        "top_k_per_role": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 25,
                            "default": 5,
                        },
                    },
                    "required": ["intent_text"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "subsystem_hint": {"type": ["string", "null"]},
                        "buy_complete": {"type": "array"},
                        "build_from_parts": {"type": "array"},
                        "warnings": {"type": "array"},
                        "query_time_ms": {"type": "number"},
                    },
                },
                phase=2,
                resource_limits=_INTENT_LIMITS,
            ),
            handler=self.handle_search_intent,
        )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def handle_search_parametric(self, arguments: dict[str, Any]) -> dict[str, Any]:
        category = arguments.get("category")
        if not isinstance(category, str) or not category.strip():
            raise ValueError(
                "component.search_parametric: 'category' is required and must be a non-empty string"
            )

        filters = _parse_filters(arguments.get("filters"))

        limit = arguments.get("limit", 25)
        try:
            limit_int = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("component.search_parametric: 'limit' must be an integer") from exc
        if limit_int < 1 or limit_int > 200:
            raise ValueError("component.search_parametric: 'limit' must be in [1, 200]")

        with tracer.start_as_current_span("component.mcp.search_parametric") as span:
            span.set_attribute("component.category", category)
            span.set_attribute("component.filter_count", len(filters))
            result = await self._store.query(
                CatalogQuery(category=category.strip(), filters=filters, limit=limit_int)
            )
            span.set_attribute("component.result_count", len(result.rows))
            logger.info(
                "component_search_parametric",
                category=category,
                filter_count=len(filters),
                result_count=len(result.rows),
                query_time_ms=result.query_time_ms,
            )
            return {
                "rows": [_row_to_dict(row) for row in result.rows],
                "query_time_ms": result.query_time_ms,
            }

    async def handle_search_intent(self, arguments: dict[str, Any]) -> dict[str, Any]:
        intent_text = arguments.get("intent_text")
        if not isinstance(intent_text, str) or not intent_text.strip():
            raise ValueError(
                "component.search_intent: 'intent_text' is required and must be a non-empty string"
            )

        top_k = arguments.get("top_k_per_role", 5)
        try:
            top_k_int = int(top_k)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "component.search_intent: 'top_k_per_role' must be an integer"
            ) from exc
        if top_k_int < 1 or top_k_int > 25:
            raise ValueError("component.search_intent: 'top_k_per_role' must be in [1, 25]")

        async def _search_parametric(
            category: str, filters: list[ComponentFilter], limit: int
        ) -> Any:
            return await self._store.query(
                CatalogQuery(category=category, filters=filters, limit=limit)
            )

        with tracer.start_as_current_span("component.mcp.search_intent") as span:
            span.set_attribute("component.intent_length", len(intent_text))
            result = await search_intent(
                intent_text=intent_text.strip(),
                llm=self._llm,
                search_parametric=_search_parametric,
                knowledge_service=self._knowledge_service,
                subsystem_templates=self._subsystem_templates,
                known_categories=self._known_categories,
                top_k_per_role=top_k_int,
            )
            span.set_attribute("component.buy_complete_count", len(result.buy_complete))
            span.set_attribute("component.build_from_parts_count", len(result.build_from_parts))
            span.set_attribute("component.warning_count", len(result.warnings))
            logger.info(
                "component_search_intent",
                intent_length=len(intent_text),
                subsystem=result.subsystem_hint,
                buy_complete_count=len(result.buy_complete),
                build_from_parts_count=len(result.build_from_parts),
                warning_count=len(result.warnings),
                query_time_ms=result.query_time_ms,
            )
            return intent_to_dict(result)


def _parse_filters(raw: Any) -> list[ComponentFilter]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(
            "component.search_parametric: 'filters' must be a list of {property, op, value} objects"
        )
    out: list[ComponentFilter] = []
    for i, f in enumerate(raw):
        if not isinstance(f, dict):
            raise ValueError(f"component.search_parametric: filters[{i}] must be an object")
        prop = f.get("property")
        op = f.get("op")
        if not isinstance(prop, str) or not prop.strip():
            raise ValueError(
                f"component.search_parametric: filters[{i}].property must be a non-empty string"
            )
        if op not in _ALLOWED_FILTER_OPS:
            raise ValueError(
                f"component.search_parametric: filters[{i}].op must be one of "
                f"{_ALLOWED_FILTER_OPS}; got {op!r}"
            )
        if "value" not in f:
            raise ValueError(f"component.search_parametric: filters[{i}].value is required")
        out.append(ComponentFilter(property=prop.strip(), op=op, value=f["value"]))
    return out


def _row_to_dict(row: ComponentCatalogRow) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "mpn": row.mpn,
        "manufacturer": row.manufacturer,
        "category": row.category,
        "purchase_unit": row.purchase_unit,
        "cost_usd": row.cost_usd,
        "lifecycle": row.lifecycle,
        "datasheet_url": row.datasheet_url,
        "specs": row.specs,
        "extraction_meta": row.extraction_meta,
        "schema_version": row.schema_version,
        "indexed_at": row.indexed_at.isoformat(),
    }


__all__ = ["ComponentServer"]
