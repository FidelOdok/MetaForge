"""Clearance / interference check between two real committed parts
(FORGE-272, gap G-D4).

``cadquery.boolean_operation`` already supports ``operation="intersect"``
(``tool_registry/tools/cadquery/operations.py``) -- a real OCCT boolean
intersection between two solids, already returning the intersection's
``result_volume``/``result_area``. No new geometric algorithm is needed:
an interference check between two parts is exactly this call on their two
real committed STEP files, with ``result_volume > 0`` meaning they overlap
in 3D space, and the volume itself the real overlap magnitude.

Mirrors ``api_gateway.twin.manufacture_release``/``geometry_diff``'s exact
shape: a factory closure over ``twin`` + an injected ``blob_stager`` + a
lazily-bound ``mcp_bridge``, called from a thin REST route -- no MCP tool
registration, so this has none of ``bootstrap_tool_registry``'s
signature-drift risk (FORGE-405/298 both hit that class of bug; this
capability is REST-only, like ``manufacture_release``/``geometry_diff``
themselves).

Deliberately out of scope (separate future work, not guessed at here):

- **ISO 286 tolerance grades / hole-shaft fit classification** (H7/g6-style)
  -- real, nontrivial standard-table work with no real use case identified
  on this project today; the only existing touchpoint
  (``domain_agents/mechanical/skills/check_tolerance``) takes a free-text
  ``tolerance_grade`` label from the caller, it does not implement ISO 286
  itself.
- **All-pairs sweep across an entire assembly** -- this checks exactly the
  two parts a caller names, not every combination of parts in a hierarchy.
  A sweep is a straightforward loop over this primitive once it's proven,
  not required to make this PR's "across the assembly" honestly useful for
  the pairwise case that actually matters (e.g. "does this actuator clash
  with its mounting bracket").
- **3D interference highlighting in the viewer** -- no existing
  visualization precedent exists anywhere in this codebase for any
  analysis result (confirmed independently by FORGE-273/282/294/301's own
  scoping); this returns a pass/fail + volume, not a rendered overlay.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.interference_check")

#: ``cadquery.boolean_operation`` only imports STEP source geometry; a
#: non-STEP source is rejected with a clear message rather than failing
#: confusingly downstream (same convention ``manufacture_release.py`` and
#: ``geometry_diff.py`` both established).
_SUPPORTED_SOURCE_FORMATS = {"step", "stp"}


def make_interference_check(twin: Any, *, blob_stager: Any, mcp_bridge: Any) -> Any:
    """Return an async ``check(*, work_product_id_a, work_product_id_b) -> dict``
    bound to a twin + blob stager + mcp_bridge (a real
    ``cadquery.boolean_operation(operation="intersect")`` call).
    """

    async def check(*, work_product_id_a: str, work_product_id_b: str) -> dict[str, Any]:
        with tracer.start_as_current_span("interference.check") as span:
            span.set_attribute("interference.work_product_id_a", work_product_id_a)
            span.set_attribute("interference.work_product_id_b", work_product_id_b)

            try:
                uid_a = UUID(work_product_id_a)
                uid_b = UUID(work_product_id_b)
            except ValueError as exc:
                raise ValueError(
                    "twin.check_interference: invalid work_product_id "
                    f"({work_product_id_a!r}, {work_product_id_b!r})"
                ) from exc

            wp_a = await twin.get_work_product(uid_a)
            if wp_a is None:
                raise ValueError(f"twin.check_interference: no work_product {work_product_id_a!r}")
            wp_b = await twin.get_work_product(uid_b)
            if wp_b is None:
                raise ValueError(f"twin.check_interference: no work_product {work_product_id_b!r}")

            for label, wp in (("A", wp_a), ("B", wp_b)):
                fmt = (wp.format or "").lower()
                if fmt not in _SUPPORTED_SOURCE_FORMATS:
                    raise ValueError(
                        f"twin.check_interference: part {label} has format {wp.format!r}, "
                        f"expected a STEP file ({'/'.join(sorted(_SUPPORTED_SOURCE_FORMATS))}) -- "
                        "cadquery.boolean_operation only imports STEP source geometry"
                    )

            staged_a = await blob_stager(str(uid_a))
            staged_b = await blob_stager(str(uid_b))

            result = await mcp_bridge.invoke(
                "cadquery.boolean_operation",
                {
                    "input_file_a": staged_a["file_path"],
                    "input_file_b": staged_b["file_path"],
                    "operation": "intersect",
                },
            )

            volume = float(result.get("result_volume") or 0.0)
            area = float(result.get("result_area") or 0.0)
            interferes = volume > 0.0

            logger.info(
                "interference_checked",
                work_product_id_a=work_product_id_a,
                work_product_id_b=work_product_id_b,
                interferes=interferes,
                interference_volume_mm3=volume,
            )
            span.set_attribute("interference.interferes", interferes)
            span.set_attribute("interference.volume_mm3", volume)

            return {
                "work_product_id_a": work_product_id_a,
                "work_product_id_b": work_product_id_b,
                "interferes": interferes,
                "interference_volume_mm3": round(volume, 4),
                "interference_area_mm2": round(area, 4),
            }

    return check
