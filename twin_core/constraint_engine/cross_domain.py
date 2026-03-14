"""Cross-domain constraint validation for the Digital Twin.

Validates constraints that span multiple engineering domains — for example,
verifying that a PCB fits within a mechanical enclosure, that mounting holes
align, or that thermal zones don't conflict between domains.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from twin_core.api import TwinAPI


class CrossDomainCheck(BaseModel):
    """A cross-domain validation check result."""

    name: str
    domain_a: str  # e.g., "mechanical"
    domain_b: str  # e.g., "electronics"
    passed: bool
    message: str
    severity: str = "error"  # "error", "warning", "info"
    details: dict[str, Any] = Field(default_factory=dict)


class CrossDomainValidator:
    """Validates constraints that span multiple engineering domains.

    Queries the Digital Twin for work_products from different domains and runs
    cross-domain checks to ensure physical, thermal, and spatial consistency.
    """

    def __init__(self, twin: TwinAPI) -> None:
        self.twin = twin
        self.logger = structlog.get_logger()
        self._checks: list[Callable[..., Any]] = []
        self._register_default_checks()

    def _register_default_checks(self) -> None:
        """Register the built-in cross-domain checks."""
        self._checks = [
            self.check_pcb_enclosure_fit,
            self.check_mounting_hole_alignment,
            self.check_thermal_zones,
            self.check_connector_clearances,
        ]

    def register_check(self, check_fn: Callable[..., Any]) -> None:
        """Register a custom cross-domain check function.

        The function must accept (work_product_id: UUID, branch: str) and return
        a CrossDomainCheck.
        """
        self._checks.append(check_fn)

    async def validate_all(
        self, work_product_id: UUID, branch: str = "main"
    ) -> list[CrossDomainCheck]:
        """Run all cross-domain checks for an work_product.

        Each check runs independently. If a check raises an exception, it is
        caught and reported as a failed check with severity "error".
        """
        results: list[CrossDomainCheck] = []
        for check in self._checks:
            try:
                result = await check(work_product_id, branch)
                results.append(result)
            except Exception as e:
                self.logger.error(
                    "Cross-domain check failed with exception",
                    check_name=getattr(check, "__name__", str(check)),
                    error=str(e),
                )
                results.append(
                    CrossDomainCheck(
                        name=getattr(check, "__name__", str(check)),
                        domain_a="unknown",
                        domain_b="unknown",
                        passed=False,
                        message=str(e),
                        severity="error",
                    )
                )
        return results

    async def check_pcb_enclosure_fit(
        self, work_product_id: UUID, branch: str = "main"
    ) -> CrossDomainCheck:
        """Verify PCB dimensions fit within mechanical enclosure.

        Looks for PCB work_products (electronics domain) and enclosure work_products
        (mechanical domain) and compares their dimensions.
        """
        pcb_artifacts = await self.twin.list_work_products(branch=branch, domain="electronics")
        mech_work_products = await self.twin.list_work_products(branch=branch, domain="mechanical")

        # Find PCB and enclosure by metadata
        pcb = None
        enclosure = None
        for a in pcb_artifacts:
            if a.metadata.get("subtype") == "pcb":
                pcb = a
                break

        for a in mech_work_products:
            if a.metadata.get("subtype") == "enclosure":
                enclosure = a
                break

        if pcb is None or enclosure is None:
            return CrossDomainCheck(
                name="check_pcb_enclosure_fit",
                domain_a="electronics",
                domain_b="mechanical",
                passed=True,
                message="PCB or enclosure work_product not found — skipping check",
                severity="info",
                details={"pcb_found": pcb is not None, "enclosure_found": enclosure is not None},
            )

        pcb_dims = pcb.metadata.get("dimensions", {})
        enc_dims = enclosure.metadata.get("dimensions", {})
        enc_clearance = enclosure.metadata.get("internal_clearance", 0.0)

        pcb_width = pcb_dims.get("width", 0.0)
        pcb_height = pcb_dims.get("height", 0.0)
        enc_width = enc_dims.get("width", 0.0)
        enc_height = enc_dims.get("height", 0.0)

        # Available internal space = enclosure dimension - 2 * clearance
        available_width = enc_width - 2 * enc_clearance
        available_height = enc_height - 2 * enc_clearance

        fits_width = pcb_width <= available_width
        fits_height = pcb_height <= available_height
        passed = fits_width and fits_height

        details = {
            "pcb_width": pcb_width,
            "pcb_height": pcb_height,
            "enclosure_width": enc_width,
            "enclosure_height": enc_height,
            "available_width": available_width,
            "available_height": available_height,
            "fits_width": fits_width,
            "fits_height": fits_height,
        }

        if passed:
            message = (
                f"PCB ({pcb_width}x{pcb_height}mm) fits within enclosure "
                f"({available_width}x{available_height}mm available)"
            )
        else:
            oversize = []
            if not fits_width:
                oversize.append(f"width ({pcb_width}mm > {available_width}mm)")
            if not fits_height:
                oversize.append(f"height ({pcb_height}mm > {available_height}mm)")
            message = f"PCB exceeds enclosure in: {', '.join(oversize)}"

        return CrossDomainCheck(
            name="check_pcb_enclosure_fit",
            domain_a="electronics",
            domain_b="mechanical",
            passed=passed,
            message=message,
            severity="error" if not passed else "info",
            details=details,
        )

    async def check_mounting_hole_alignment(
        self, work_product_id: UUID, branch: str = "main"
    ) -> CrossDomainCheck:
        """Verify PCB mounting holes align with enclosure mounting points.

        Compares mounting hole positions from PCB metadata with mounting
        standoff positions from enclosure metadata.
        """
        pcb_artifacts = await self.twin.list_work_products(branch=branch, domain="electronics")
        mech_work_products = await self.twin.list_work_products(branch=branch, domain="mechanical")

        pcb = None
        enclosure = None
        for a in pcb_artifacts:
            if a.metadata.get("subtype") == "pcb":
                pcb = a
                break
        for a in mech_work_products:
            if a.metadata.get("subtype") == "enclosure":
                enclosure = a
                break

        if pcb is None or enclosure is None:
            return CrossDomainCheck(
                name="check_mounting_hole_alignment",
                domain_a="electronics",
                domain_b="mechanical",
                passed=True,
                message="PCB or enclosure work_product not found — skipping check",
                severity="info",
                details={"pcb_found": pcb is not None, "enclosure_found": enclosure is not None},
            )

        pcb_holes: list[dict[str, float]] = pcb.metadata.get("mounting_holes", [])
        enc_standoffs: list[dict[str, float]] = enclosure.metadata.get("mounting_standoffs", [])

        if not pcb_holes or not enc_standoffs:
            return CrossDomainCheck(
                name="check_mounting_hole_alignment",
                domain_a="electronics",
                domain_b="mechanical",
                passed=True,
                message="No mounting holes or standoffs defined — skipping check",
                severity="info",
                details={
                    "pcb_holes_count": len(pcb_holes),
                    "enc_standoffs_count": len(enc_standoffs),
                },
            )

        tolerance = enclosure.metadata.get("mounting_tolerance", 0.5)  # mm
        misaligned: list[dict[str, Any]] = []
        matched = 0

        for hole in pcb_holes:
            hx, hy = hole.get("x", 0.0), hole.get("y", 0.0)
            best_dist = float("inf")
            for standoff in enc_standoffs:
                sx, sy = standoff.get("x", 0.0), standoff.get("y", 0.0)
                dist = ((hx - sx) ** 2 + (hy - sy) ** 2) ** 0.5
                if dist < best_dist:
                    best_dist = dist
            if best_dist <= tolerance:
                matched += 1
            else:
                misaligned.append({"hole": hole, "min_distance": round(best_dist, 3)})

        passed = len(misaligned) == 0

        details = {
            "total_holes": len(pcb_holes),
            "matched": matched,
            "misaligned": misaligned,
            "tolerance_mm": tolerance,
        }

        if passed:
            message = (
                f"All {len(pcb_holes)} mounting holes align with standoffs "
                f"(tolerance {tolerance}mm)"
            )
        else:
            message = (
                f"{len(misaligned)} of {len(pcb_holes)} mounting holes "
                f"do not align with enclosure standoffs (tolerance {tolerance}mm)"
            )

        return CrossDomainCheck(
            name="check_mounting_hole_alignment",
            domain_a="electronics",
            domain_b="mechanical",
            passed=passed,
            message=message,
            severity="error" if not passed else "info",
            details=details,
        )

    async def check_thermal_zones(
        self, work_product_id: UUID, branch: str = "main"
    ) -> CrossDomainCheck:
        """Validate thermal zones don't conflict between domains.

        Checks that high-power electronic components are not placed in
        thermally restricted mechanical zones (e.g., near battery, near
        plastic walls with low melting points).
        """
        pcb_artifacts = await self.twin.list_work_products(branch=branch, domain="electronics")
        mech_work_products = await self.twin.list_work_products(branch=branch, domain="mechanical")

        pcb = None
        enclosure = None
        for a in pcb_artifacts:
            if a.metadata.get("subtype") == "pcb":
                pcb = a
                break
        for a in mech_work_products:
            if a.metadata.get("subtype") == "enclosure":
                enclosure = a
                break

        if pcb is None or enclosure is None:
            return CrossDomainCheck(
                name="check_thermal_zones",
                domain_a="electronics",
                domain_b="mechanical",
                passed=True,
                message="PCB or enclosure work_product not found — skipping check",
                severity="info",
            )

        hot_zones: list[dict[str, Any]] = pcb.metadata.get("thermal_zones", [])
        restricted_zones: list[dict[str, Any]] = enclosure.metadata.get(
            "thermal_restricted_zones", []
        )

        if not hot_zones or not restricted_zones:
            return CrossDomainCheck(
                name="check_thermal_zones",
                domain_a="electronics",
                domain_b="mechanical",
                passed=True,
                message="No thermal zones defined — skipping check",
                severity="info",
            )

        conflicts: list[dict[str, Any]] = []

        for hz in hot_zones:
            hz_temp = hz.get("max_temperature", 0.0)
            hz_x, hz_y = hz.get("x", 0.0), hz.get("y", 0.0)
            hz_radius = hz.get("radius", 0.0)

            for rz in restricted_zones:
                rz_max_temp = rz.get("max_allowed_temperature", 60.0)
                rz_x, rz_y = rz.get("x", 0.0), rz.get("y", 0.0)
                rz_radius = rz.get("radius", 0.0)

                dist = ((hz_x - rz_x) ** 2 + (hz_y - rz_y) ** 2) ** 0.5
                overlap = (hz_radius + rz_radius) - dist

                if overlap > 0 and hz_temp > rz_max_temp:
                    conflicts.append(
                        {
                            "hot_zone": hz.get("name", "unnamed"),
                            "restricted_zone": rz.get("name", "unnamed"),
                            "hot_zone_temp": hz_temp,
                            "max_allowed_temp": rz_max_temp,
                            "overlap_mm": round(overlap, 2),
                        }
                    )

        passed = len(conflicts) == 0

        if passed:
            message = "No thermal zone conflicts between electronics and mechanical"
        else:
            message = (
                f"{len(conflicts)} thermal zone conflict(s) detected: "
                f"hot electronics zones overlap with thermally restricted areas"
            )

        return CrossDomainCheck(
            name="check_thermal_zones",
            domain_a="electronics",
            domain_b="mechanical",
            passed=passed,
            message=message,
            severity="warning" if not passed else "info",
            details={"conflicts": conflicts},
        )

    async def check_connector_clearances(
        self, work_product_id: UUID, branch: str = "main"
    ) -> CrossDomainCheck:
        """Ensure connector positions have adequate clearance in enclosure.

        Verifies that connectors on the PCB have corresponding cutouts in the
        enclosure and that there is sufficient clearance around each connector.
        """
        pcb_artifacts = await self.twin.list_work_products(branch=branch, domain="electronics")
        mech_work_products = await self.twin.list_work_products(branch=branch, domain="mechanical")

        pcb = None
        enclosure = None
        for a in pcb_artifacts:
            if a.metadata.get("subtype") == "pcb":
                pcb = a
                break
        for a in mech_work_products:
            if a.metadata.get("subtype") == "enclosure":
                enclosure = a
                break

        if pcb is None or enclosure is None:
            return CrossDomainCheck(
                name="check_connector_clearances",
                domain_a="electronics",
                domain_b="mechanical",
                passed=True,
                message="PCB or enclosure work_product not found — skipping check",
                severity="info",
            )

        connectors: list[dict[str, Any]] = pcb.metadata.get("connectors", [])
        cutouts: list[dict[str, Any]] = enclosure.metadata.get("cutouts", [])

        if not connectors:
            return CrossDomainCheck(
                name="check_connector_clearances",
                domain_a="electronics",
                domain_b="mechanical",
                passed=True,
                message="No connectors defined on PCB — skipping check",
                severity="info",
            )

        min_clearance = enclosure.metadata.get("min_connector_clearance", 0.5)  # mm
        issues: list[dict[str, Any]] = []

        for conn in connectors:
            conn_name = conn.get("name", "unnamed")
            conn_width = conn.get("width", 0.0)
            conn_height = conn.get("height", 0.0)

            # Find matching cutout
            matched_cutout = None
            for cutout in cutouts:
                cutout_name = cutout.get("connector_name", "")
                if cutout_name == conn_name:
                    matched_cutout = cutout
                    break

            if matched_cutout is None:
                issues.append(
                    {
                        "connector": conn_name,
                        "issue": "no_cutout",
                        "message": f"No enclosure cutout found for connector '{conn_name}'",
                    }
                )
                continue

            cutout_width = matched_cutout.get("width", 0.0)
            cutout_height = matched_cutout.get("height", 0.0)

            width_clearance = (cutout_width - conn_width) / 2
            height_clearance = (cutout_height - conn_height) / 2

            if width_clearance < min_clearance:
                issues.append(
                    {
                        "connector": conn_name,
                        "issue": "insufficient_width_clearance",
                        "clearance_mm": round(width_clearance, 2),
                        "required_mm": min_clearance,
                    }
                )

            if height_clearance < min_clearance:
                issues.append(
                    {
                        "connector": conn_name,
                        "issue": "insufficient_height_clearance",
                        "clearance_mm": round(height_clearance, 2),
                        "required_mm": min_clearance,
                    }
                )

        passed = len(issues) == 0

        if passed:
            message = (
                f"All {len(connectors)} connectors have adequate clearance (min {min_clearance}mm)"
            )
        else:
            message = f"{len(issues)} connector clearance issue(s) detected"

        return CrossDomainCheck(
            name="check_connector_clearances",
            domain_a="electronics",
            domain_b="mechanical",
            passed=passed,
            message=message,
            severity="error" if not passed else "info",
            details={"issues": issues, "min_clearance_mm": min_clearance},
        )
