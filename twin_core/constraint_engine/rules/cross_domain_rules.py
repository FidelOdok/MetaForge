"""Pre-defined cross-domain constraint rules.

These rules define the specific cross-domain checks that MetaForge validates
across engineering disciplines. Each rule is a function that can be registered
with the CrossDomainValidator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from twin_core.constraint_engine.cross_domain import CrossDomainCheck

if TYPE_CHECKING:
    from twin_core.api import TwinAPI


async def check_total_weight_budget(
    twin: TwinAPI, artifact_id: UUID, branch: str = "main"
) -> CrossDomainCheck:
    """Verify total assembly weight stays within budget.

    Sums weights from both mechanical and electronics domains and compares
    against the weight budget defined in mechanical metadata.
    """
    mech_artifacts = await twin.list_artifacts(branch=branch, domain="mechanical")
    elec_artifacts = await twin.list_artifacts(branch=branch, domain="electronics")

    total_weight = 0.0
    weight_budget = 0.0
    component_weights: dict[str, float] = {}

    for a in mech_artifacts:
        w = a.metadata.get("weight_grams", 0.0)
        total_weight += w
        if w > 0:
            component_weights[a.name] = w
        if a.metadata.get("subtype") == "enclosure":
            weight_budget = a.metadata.get("weight_budget_grams", 0.0)

    for a in elec_artifacts:
        w = a.metadata.get("weight_grams", 0.0)
        total_weight += w
        if w > 0:
            component_weights[a.name] = w

    if weight_budget <= 0:
        return CrossDomainCheck(
            name="check_total_weight_budget",
            domain_a="mechanical",
            domain_b="electronics",
            passed=True,
            message="No weight budget defined — skipping check",
            severity="info",
        )

    passed = total_weight <= weight_budget

    return CrossDomainCheck(
        name="check_total_weight_budget",
        domain_a="mechanical",
        domain_b="electronics",
        passed=passed,
        message=(
            f"Total weight {total_weight:.1f}g "
            f"{'within' if passed else 'exceeds'} budget of {weight_budget:.1f}g"
        ),
        severity="warning" if not passed else "info",
        details={
            "total_weight_grams": total_weight,
            "weight_budget_grams": weight_budget,
            "component_weights": component_weights,
        },
    )


async def check_power_thermal_consistency(
    twin: TwinAPI, artifact_id: UUID, branch: str = "main"
) -> CrossDomainCheck:
    """Verify that total power dissipation is consistent with thermal design.

    Checks that the thermal solution (heatsink, airflow) can handle
    the total power dissipation from electronic components.
    """
    elec_artifacts = await twin.list_artifacts(branch=branch, domain="electronics")
    mech_artifacts = await twin.list_artifacts(branch=branch, domain="mechanical")

    total_power_w = 0.0
    for a in elec_artifacts:
        total_power_w += a.metadata.get("power_dissipation_w", 0.0)

    thermal_capacity_w = 0.0
    for a in mech_artifacts:
        thermal_capacity_w += a.metadata.get("thermal_capacity_w", 0.0)

    if total_power_w <= 0 or thermal_capacity_w <= 0:
        return CrossDomainCheck(
            name="check_power_thermal_consistency",
            domain_a="electronics",
            domain_b="mechanical",
            passed=True,
            message="Power or thermal capacity data not available — skipping check",
            severity="info",
        )

    passed = total_power_w <= thermal_capacity_w

    return CrossDomainCheck(
        name="check_power_thermal_consistency",
        domain_a="electronics",
        domain_b="mechanical",
        passed=passed,
        message=(
            f"Total power dissipation {total_power_w:.1f}W "
            f"{'within' if passed else 'exceeds'} "
            f"thermal capacity of {thermal_capacity_w:.1f}W"
        ),
        severity="error" if not passed else "info",
        details={
            "total_power_w": total_power_w,
            "thermal_capacity_w": thermal_capacity_w,
        },
    )
