"""Interface-quantity measurement recording for twin.record_measurement
(FORGE-321, target lifecycle spec App. B REALISE/LEARN, step 11).

``InterfaceQuantity`` has no node id of its own -- it's embedded in
``ArchInterface.quantities`` inside a single ``SYSTEM_ARCHITECTURE``
WorkProduct's metadata (``twin_core/models/interface.py``'s own docstring:
"not a new graph node type"). So "record a measurement on an interface
quantity" means: find that WorkProduct (explicit ``work_product_id``, or
the project's one SYSTEM_ARCHITECTURE doc -- FORGE-313's own established
one-per-project assumption), find the ``{from, to}`` interface and its
named ``metric`` inside it, and append a ``MeasuredValue`` there --
persisted by rewriting the WorkProduct's ``metadata`` (field-level
replace, same as every other structured-document recorder in this
package).

The device that took the measurement is linked via a real
``DeviceInstance --MEASURED_BY--> WorkProduct`` edge (FORGE-321's own new
edge type) -- the edge names the document holding the quantity, since the
quantity itself has no node to point an edge at; the measurement's own
``interface``/``metric`` metadata on that edge disambiguates which one.

Residual computation (predicted vs. measured, feeding FORGE-321's
calibration store) needs a real predicted value. Rather than silently
trusting a possibly-stale or absent ``quantity.predicted`` field, the
caller supplies ``predicted_value``/``predicted_tier`` explicitly (e.g.
from a fresh ``twin.evaluate_metric`` call made just before this one --
the natural, honest composition, not a shortcut this module takes for
the caller). When the interface quantity's own ``predicted`` is still
unset, this measurement also backfills it -- the first real prediction a
quantity gets is exactly whatever the caller most recently computed.
Omitting ``predicted_value`` records the measurement but skips residual
calibration entirely (never fabricates a predicted value to compare
against).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer
from twin_core.models.enums import EdgeType, WorkProductType

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.measurement_recorder")


async def _resolve_system_architecture_wp(
    twin: Any, *, work_product_id: str | None, project_id: str | None
) -> Any:
    if work_product_id:
        wp = await twin.get_work_product(UUID(work_product_id))
        if wp is None:
            raise ValueError(f"twin.record_measurement: no work_product {work_product_id!r}")
        return wp
    if not project_id:
        raise ValueError(
            "twin.record_measurement: 'work_product_id' or 'project_id' is required "
            "(to find the project's system_architecture document)"
        )
    candidates = await twin.list_work_products(
        work_product_type=WorkProductType.SYSTEM_ARCHITECTURE, project_id=UUID(project_id)
    )
    if not candidates:
        raise ValueError(
            f"twin.record_measurement: project {project_id!r} has no system_architecture "
            "work product to hold interface quantities"
        )
    if len(candidates) > 1:
        raise ValueError(
            f"twin.record_measurement: project {project_id!r} has {len(candidates)} "
            "system_architecture work products -- pass 'work_product_id' explicitly to "
            "disambiguate"
        )
    return candidates[0]


def make_measurement_recorder(twin: Any, *, calibration_recorder: Any = None) -> Any:
    """Return an async ``record(...)`` bound to a twin + (optional)
    calibration residual recorder."""

    async def record(
        *,
        device_instance_id: str,
        from_component: str,
        to_component: str,
        metric: str,
        value: float,
        unit: str = "mm",
        source: str = "",
        timestamp: str = "",
        work_product_id: str | None = None,
        project_id: str | None = None,
        predicted_value: float | None = None,
        predicted_tier: int = 0,
    ) -> dict[str, Any]:
        if not device_instance_id:
            raise ValueError("twin.record_measurement: 'device_instance_id' is required")
        if not from_component or not to_component:
            raise ValueError(
                "twin.record_measurement: 'from_component' and 'to_component' are required"
            )
        if not metric:
            raise ValueError("twin.record_measurement: 'metric' is required")

        with tracer.start_as_current_span("twin.record_measurement") as span:
            device_id = UUID(device_instance_id)
            device = await twin.get_device_instance(device_id)
            if device is None:
                raise ValueError(
                    f"twin.record_measurement: no device_instance {device_instance_id!r}"
                )

            wp = await _resolve_system_architecture_wp(
                twin, work_product_id=work_product_id, project_id=project_id
            )
            interfaces = list(wp.metadata.get("interfaces") or [])
            interface_idx = next(
                (
                    i
                    for i, iface in enumerate(interfaces)
                    if iface.get("from") == from_component and iface.get("to") == to_component
                ),
                None,
            )
            if interface_idx is None:
                raise ValueError(
                    f"twin.record_measurement: no interface {from_component!r} -> "
                    f"{to_component!r} on work product {wp.id}"
                )
            interface = dict(interfaces[interface_idx])
            quantities = list(interface.get("quantities") or [])
            quantity_idx = next(
                (i for i, q in enumerate(quantities) if q.get("metric") == metric), None
            )
            if quantity_idx is None:
                raise ValueError(
                    f"twin.record_measurement: interface {from_component!r} -> "
                    f"{to_component!r} has no quantity for metric {metric!r}"
                )
            quantity = dict(quantities[quantity_idx])

            measured_entry = {"value": value, "source": source, "timestamp": timestamp}
            quantity["measured"] = [*quantity.get("measured", []), measured_entry]

            residual_out: dict[str, Any] = {}
            effective_predicted = predicted_value
            if effective_predicted is None and quantity.get("predicted"):
                effective_predicted = quantity["predicted"].get("value")
            if predicted_value is not None and not quantity.get("predicted"):
                # Backfill: the first real prediction this quantity has ever
                # gotten is exactly what the caller just supplied.
                quantity["predicted"] = {
                    "value": predicted_value,
                    "band": None,
                    "tier": str(predicted_tier),
                    "evidence": "",
                }

            quantities[quantity_idx] = quantity
            interface["quantities"] = quantities
            interfaces[interface_idx] = interface
            new_metadata = {**wp.metadata, "interfaces": interfaces}
            await twin.update_work_product(wp.id, {"metadata": new_metadata})

            await twin.add_edge(
                device_id,
                wp.id,
                EdgeType.MEASURED_BY,
                metadata={
                    "interface": f"{from_component}->{to_component}",
                    "metric": metric,
                    "value": value,
                },
            )

            if effective_predicted is not None and calibration_recorder is not None:
                residual_out = await calibration_recorder(
                    metric=metric,
                    tier=predicted_tier,
                    predicted=effective_predicted,
                    measured=value,
                    unit=unit,
                    project_id=project_id,
                    valid_against=[{"ref": str(wp.id), "entity_kind": "work_product"}],
                )

            span.set_attribute("measurement.metric", metric)
            span.set_attribute("measurement.value", value)
            logger.info(
                "measurement_recorded",
                device_instance_id=device_instance_id,
                work_product_id=str(wp.id),
                interface=f"{from_component}->{to_component}",
                metric=metric,
                value=value,
                residual=residual_out.get("residual"),
            )
            return {
                "work_product_id": str(wp.id),
                "interface": f"{from_component}->{to_component}",
                "metric": metric,
                "value": value,
                **residual_out,
            }

    return record
