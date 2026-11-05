"""The slicer cross-check after the exact FDM checks (DFM-007, DFM-014).

When a component is FDM and its manufacturing context names a machine from
the machine library, the exact DFM job slices the validated revision's STEP
with ``slicer.slice`` (PrusaSlicer, over MCP through the same bridge the
exact measurement uses) and records what the slicer said as evidence on the
exact finding set:

* the slicer's print time, marked as an estimate: PrusaSlicer's own figure for
  a generic printer sized like the selected machine, not a quotation;
* the support volume, as the difference between a slice with automatic
  supports and one without (both the slicer's own filament figures);
* filament, layers, height and the slicer's warnings.

**It never decides anything.** The record carries ``affects_status: false``
and is attached after :func:`twin_core.dfm.exact.evaluate_exact` set the
status; no slicer outcome can make a set pass, and a slicer that cannot run
leaves the exact findings exactly as they were.

**When it does not run** the evidence says ``not_evaluated`` with the reason
(``unavailable``, ``failed``, ``no_machine``, ``orientation``, ``no_step``,
``disabled``): never an empty field that reads as nothing to report.

``METAFORGE_DFM_SLICER_CHECK=off`` switches the cross-check off.
"""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.dfm_slicer")

SLICER_TOOL = "slicer.slice"
SLICER_CHECK_ENV = "METAFORGE_DFM_SLICER_CHECK"
_STAGE_SUBDIR = "_dfm_slicer"

#: The slicer adapter's built-in generic printers, by bed (width, depth, height mm).
#: Mirrors ``tool_registry.tools.slicer.adapter.PRINTERS``; the gateway never
#: imports an adapter, so a test keeps the two in step.
SLICER_PRINTERS: dict[str, tuple[float, float, float]] = {
    "generic_220": (220.0, 220.0, 250.0),
    "generic_250": (250.0, 210.0, 220.0),
    "generic_256": (256.0, 256.0, 256.0),
    "generic_300": (300.0, 300.0, 400.0),
}
#: The adapter's built-in material presets.
SLICER_MATERIALS: frozenset[str] = frozenset({"PLA", "PETG", "ABS", "ASA", "TPU"})

ESTIMATE_NOTE = (
    "The slicer's own estimate for a generic printer sized like the selected machine; "
    "not a quotation and not the machine's own figure."
)
NEVER_DECIDES = "Evidence beside the exact checks; it never changes the analysis status."

_UNAVAILABLE_WORDS = (
    "not found",
    "not served",
    "not available",
    "notavailable",
    "unavailable",
    "no adapter",
    "connection",
    "unreachable",
    "timed out",
    "timeout",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def enabled() -> bool:
    """Whether the cross-check runs at all (on unless switched off)."""
    value = os.getenv(SLICER_CHECK_ENV, "on").strip().lower()
    return value not in ("off", "0", "false", "no", "disabled")


def _metric(outcome: str, reason: str) -> None:
    from api_gateway.twin.dfm_findings import _metric as metric

    metric("record_dfm_slicer_check", outcome, reason)


def printer_for(build_volume: Any) -> tuple[str, str]:
    """``(printer, basis)``: the smallest generic printer the machine's build volume fits in."""
    dims: list[float] | None = None
    if isinstance(build_volume, list | tuple) and len(build_volume) == 3:
        try:
            dims = [float(v) for v in build_volume]
        except (TypeError, ValueError):
            dims = None
    if dims is None:
        return "generic_220", "the machine's build volume is not recorded; generic 220 mm bed"
    foot, height = sorted(dims[:2]), dims[2]
    fitting = [
        (size[0] * size[1] * size[2], name)
        for name, size in SLICER_PRINTERS.items()
        if all(m <= p + 1e-9 for m, p in zip(foot, sorted(size[:2]), strict=True))
        and height <= size[2] + 1e-9
    ]
    shown = " x ".join(f"{d:g}" for d in dims)
    if fitting:
        name = min(fitting)[1]
        return name, f"generic printer {name} (the smallest preset that holds {shown} mm)"
    name = max(SLICER_PRINTERS, key=lambda n: SLICER_PRINTERS[n][0] * SLICER_PRINTERS[n][1])
    return name, f"generic printer {name} (no preset is as large as {shown} mm)"


def material_for(material: str | None) -> tuple[str | None, str]:
    """``(slicer material, basis)``; ``None`` lets the adapter use its default."""
    if not material:
        return None, "no material is selected; the slicer's default preset"
    name = material.strip().upper()
    if name in SLICER_MATERIALS:
        return name, f"{name} preset"
    return None, f"{material} has no slicer preset; the slicer's default preset"


def _is_plus_z(direction: tuple[float, ...]) -> bool:
    return (
        len(direction) == 3
        and abs(direction[0]) < 1e-6
        and abs(direction[1]) < 1e-6
        and direction[2] > 0
    )


def not_evaluated(reason_code: str, reason: str, *, geometry_ref: str) -> dict[str, Any]:
    """The record of a cross-check that did not run, and why."""
    _metric("not_evaluated", reason_code)
    logger.info(
        "dfm_slicer_check_not_evaluated",
        geometry_ref=geometry_ref,
        reason_code=reason_code,
        reason=reason,
    )
    return {
        "check": "slicer_cross_check",
        "tool": SLICER_TOOL,
        "status": "not_evaluated",
        "reason_code": reason_code,
        "reason": reason[:400],
        "geometry_ref": geometry_ref,
        "affects_status": False,
        "note": NEVER_DECIDES,
        "evaluated_at": _now(),
    }


def _failure_code(error: str) -> str:
    lowered = error.lower()
    return "unavailable" if any(w in lowered for w in _UNAVAILABLE_WORDS) else "failed"


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


async def cross_check(
    invoke: Any,
    *,
    step: bytes | None,
    geometry_ref: str,
    machine: Any,
    material: str | None,
    build_direction: tuple[float, ...],
    workspace_dir: Path | None = None,
) -> dict[str, Any]:
    """Slice ``step`` and return the evidence record. Never raises.

    ``invoke(tool_id, params)`` calls an adapter tool over MCP and returns its
    unwrapped result (the exact CAD service's ``invoke_tool``).
    """
    with tracer.start_as_current_span("twin.dfm.slicer_cross_check") as span:
        span.set_attribute("item.ref", geometry_ref)
        if not enabled():
            span.set_attribute("dfm.slicer.status", "disabled")
            return not_evaluated(
                "disabled",
                f"the slicer cross-check is switched off ({SLICER_CHECK_ENV})",
                geometry_ref=geometry_ref,
            )
        if machine is None:
            return not_evaluated(
                "no_machine",
                "no machine from the machine library is stated in the manufacturing context",
                geometry_ref=geometry_ref,
            )
        if not _is_plus_z(tuple(build_direction)):
            shown = ", ".join(f"{float(v):g}" for v in build_direction)
            return not_evaluated(
                "orientation",
                f"the slicer slices the part as modelled (+Z up) and cannot yet be turned to "
                f"the build direction {shown}",
                geometry_ref=geometry_ref,
            )
        if not step:
            return not_evaluated(
                "no_step", f"{geometry_ref} has no stored STEP", geometry_ref=geometry_ref
            )
        caps = getattr(machine, "capabilities", None) or {}
        volume = caps.get("build_volume_mm")
        printer, printer_basis = printer_for(getattr(volume, "value", None))
        slicer_material, material_basis = material_for(material)
        root = workspace_dir or Path(os.getenv("ADAPTER_WORKSPACE_DIR", "/workspace"))
        if not root.is_dir():
            # No shared adapter volume here: the slicer could not read the file.
            return not_evaluated(
                "unavailable",
                f"no adapter workspace at {root} to hand the slicer the STEP",
                geometry_ref=geometry_ref,
            )
        stage = root / _STAGE_SUBDIR / uuid.uuid4().hex[:12]
        try:
            stage.mkdir(parents=True, exist_ok=True)
            model = stage / "part.step"
            model.write_bytes(step)
        except OSError as exc:
            span.record_exception(exc)
            return not_evaluated(
                "unavailable",
                f"the STEP could not be staged on the adapter workspace ({exc})",
                geometry_ref=geometry_ref,
            )
        params: dict[str, Any] = {"file_path": str(model), "printer": printer, "supports": True}
        if slicer_material:
            params["material"] = slicer_material
        try:
            try:
                with_supports = await invoke(SLICER_TOOL, params)
            except Exception as exc:  # noqa: BLE001 - recorded as not evaluated
                span.record_exception(exc)
                error = str(exc)[:300] or type(exc).__name__
                return not_evaluated(
                    _failure_code(error),
                    f"slicer.slice could not run: {error}",
                    geometry_ref=geometry_ref,
                )
            support_volume: float | None = None
            support_method = (
                "filament volume with automatic supports minus without, both from the slicer"
            )
            warnings = [str(m)[:300] for m in (with_supports.get("messages") or [])][:20]
            try:
                bare = await invoke(SLICER_TOOL, {**params, "supports": False})
                a = _num(with_supports.get("filament_cm3"))
                b = _num(bare.get("filament_cm3"))
                if a is not None and b is not None:
                    support_volume = round(max(0.0, a - b), 3)
                else:
                    support_method = "not measured: the slicer reported no filament volume"
            except Exception as exc:  # noqa: BLE001 - the first slice still stands
                support_method = f"not measured: the slice without supports failed ({exc})"[:300]
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        out: dict[str, Any] = {
            "check": "slicer_cross_check",
            "tool": SLICER_TOOL,
            "status": "evaluated",
            "reason_code": "sliced",
            "reason": "",
            "geometry_ref": geometry_ref,
            "affects_status": False,
            "note": NEVER_DECIDES,
            "slicer": with_supports.get("slicer"),
            "printer": printer,
            "printer_basis": printer_basis,
            "machine": getattr(machine, "name", None),
            "material": slicer_material or with_supports.get("material"),
            "material_basis": material_basis,
            "supports": "automatic",
            "print_time_s": with_supports.get("print_time_s"),
            "print_time": with_supports.get("print_time"),
            "print_time_is_estimate": True,
            "estimate_note": ESTIMATE_NOTE,
            "filament_cm3": with_supports.get("filament_cm3"),
            "filament_g": with_supports.get("filament_g"),
            "support_volume_cm3": support_volume,
            "support_volume_method": support_method,
            "layers": with_supports.get("layers"),
            "height_mm": with_supports.get("height_mm"),
            "warnings": warnings,
            "gcode_path": with_supports.get("gcode_path"),
            "evaluated_at": _now(),
        }
        _metric("evaluated", "sliced")
        span.set_attribute("dfm.slicer.status", "evaluated")
        logger.info(
            "dfm_slicer_check_evaluated",
            geometry_ref=geometry_ref,
            printer=printer,
            print_time_s=out["print_time_s"],
            support_volume_cm3=support_volume,
            warnings=len(warnings),
        )
        return out


__all__ = [
    "ESTIMATE_NOTE",
    "SLICER_CHECK_ENV",
    "SLICER_MATERIALS",
    "SLICER_PRINTERS",
    "SLICER_TOOL",
    "cross_check",
    "enabled",
    "material_for",
    "not_evaluated",
    "printer_for",
]
