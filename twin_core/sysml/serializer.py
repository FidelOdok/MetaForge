"""JSON serialization following SysML v2 REST API conventions.

The SysML v2 API uses camelCase keys, ``@type`` discriminators, and
``@id`` identifiers. This serializer handles conversion between the
Pydantic models and the wire format expected by SysML v2 API endpoints.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.sysml.models import (
    ConnectionUsage,
    ConstraintUsage,
    Package,
    PartUsage,
    RequirementUsage,
    SysMLElement,
    SysMLElementType,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("twin_core.sysml.serializer")


class SysMLSerializer:
    """Serialize and deserialize SysML v2 elements to/from JSON dicts.

    Follows the SysML v2 REST API wire format:
      - ``@id`` for element identity (UUID string)
      - ``@type`` for metatype discriminator
      - camelCase property names
    """

    def to_json(self, element: SysMLElement) -> dict[str, Any]:
        """Serialize a SysML v2 element to a JSON-compatible dict.

        Uses by_alias=True to produce ``@id``, ``@type``, ``qualifiedName``
        etc. as expected by the SysML v2 API.
        """
        with tracer.start_as_current_span("sysml.serialize") as span:
            span.set_attribute("element.type", str(element.element_type))

            data = element.model_dump(by_alias=True, mode="json")

            # Ensure UUID fields are strings
            for key, value in data.items():
                if isinstance(value, UUID):
                    data[key] = str(value)

            logger.debug(
                "element_serialized",
                element_type=str(element.element_type),
                element_id=str(element.element_id),
            )
            return data

    def from_json(self, data: dict[str, Any]) -> SysMLElement:
        """Deserialize a JSON dict to the appropriate SysML v2 element type.

        Dispatches based on the ``@type`` field in the input dict.
        """
        with tracer.start_as_current_span("sysml.deserialize") as span:
            element_type = data.get("@type", "Element")
            span.set_attribute("element.type", element_type)

            type_map: dict[str, type[SysMLElement]] = {
                SysMLElementType.PACKAGE: Package,
                SysMLElementType.PART_USAGE: PartUsage,
                SysMLElementType.REQUIREMENT_USAGE: RequirementUsage,
                SysMLElementType.CONSTRAINT_USAGE: ConstraintUsage,
                SysMLElementType.CONNECTION_USAGE: ConnectionUsage,
                SysMLElementType.ELEMENT: SysMLElement,
            }

            model_cls = type_map.get(element_type, SysMLElement)

            try:
                element = model_cls.model_validate(data)
                logger.debug(
                    "element_deserialized",
                    element_type=element_type,
                    model_class=model_cls.__name__,
                )
                return element
            except Exception as exc:
                logger.error(
                    "deserialization_failed",
                    element_type=element_type,
                    error=str(exc),
                )
                raise

    def to_json_list(self, elements: list[SysMLElement]) -> list[dict[str, Any]]:
        """Serialize a list of SysML v2 elements to JSON dicts."""
        return [self.to_json(e) for e in elements]

    def from_json_list(self, data_list: list[dict[str, Any]]) -> list[SysMLElement]:
        """Deserialize a list of JSON dicts to SysML v2 elements."""
        return [self.from_json(d) for d in data_list]

    def to_api_response(
        self, elements: list[SysMLElement], project_id: str = ""
    ) -> dict[str, Any]:
        """Format elements as a SysML v2 REST API response body.

        Follows the structure returned by ``GET /projects/{projectId}/commits/{commitId}/elements``.
        """
        with tracer.start_as_current_span("sysml.to_api_response") as span:
            span.set_attribute("element_count", len(elements))

            response = {
                "@type": "ElementList",
                "projectId": project_id,
                "elements": self.to_json_list(elements),
                "totalSize": len(elements),
            }
            logger.info(
                "api_response_formatted",
                element_count=len(elements),
                project_id=project_id,
            )
            return response
