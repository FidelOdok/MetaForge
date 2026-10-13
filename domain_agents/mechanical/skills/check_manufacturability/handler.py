"""Handler for the check_manufacturability skill (FORGE-611).

Checks a finished design's features against the chosen process's limits.
Pure arithmetic: a feature with no sourced limit for its kind, or with no
value, is ``unknown``, never a pass. Verdict: any fail is
``not_manufacturable``; otherwise any unknown, or nothing checked, is
``incomplete``; only a clean set is ``manufacturable``.
"""

from __future__ import annotations

from skill_registry.skill_base import SkillBase

from .schema import (
    CheckManufacturabilityInput,
    CheckManufacturabilityOutput,
    DesignFeature,
    DfmFinding,
    ProcessLimit,
)

_ANGLE_KINDS = {"draft_angle", "overhang_angle"}


def _unit(kind: str) -> str:
    return "deg" if kind in _ANGLE_KINDS else "mm"


def _dims(values: list[float]) -> str:
    return " x ".join(f"{v:g}" for v in values)


def _unknown(feature: DesignFeature, why: str, limit: ProcessLimit | None = None) -> DfmFinding:
    return DfmFinding(
        feature=feature.name,
        kind=feature.kind,
        status="unknown",
        basis=feature.basis,
        value=feature.value,
        limit_source=limit.source_ref if limit else "",
        message=f"{feature.name}: {why}",
    )


def _check_envelope(feature: DesignFeature, limit: ProcessLimit) -> DfmFinding:
    if feature.dims_mm is None or limit.max_dims_mm is None:
        return _unknown(feature, "envelope dimensions not given", limit)
    fits = all(
        p <= m for p, m in zip(sorted(feature.dims_mm), sorted(limit.max_dims_mm), strict=True)
    )
    return DfmFinding(
        feature=feature.name,
        kind=feature.kind,
        status="pass" if fits else "fail",
        basis=feature.basis,
        limit=f"max {_dims(limit.max_dims_mm)} mm",
        limit_source=limit.source_ref,
        message=(
            f"{feature.name}: {_dims(feature.dims_mm)} mm "
            f"{'fits within' if fits else 'exceeds'} {_dims(limit.max_dims_mm)} mm"
        ),
    )


def _check_value(feature: DesignFeature, limit: ProcessLimit) -> DfmFinding:
    if feature.value is None:
        return _unknown(feature, "no value given", limit)
    if limit.min is None and limit.max is None:
        return _unknown(feature, "the limit states neither min nor max", limit)
    unit = _unit(feature.kind)
    margins: list[float] = []
    parts: list[str] = []
    if limit.min is not None:
        margins.append(feature.value - limit.min)
        parts.append(f"min {limit.min:g} {unit}")
    if limit.max is not None:
        margins.append(limit.max - feature.value)
        parts.append(f"max {limit.max:g} {unit}")
    margin = min(margins)
    ok = margin >= 0
    return DfmFinding(
        feature=feature.name,
        kind=feature.kind,
        status="pass" if ok else "fail",
        basis=feature.basis,
        value=feature.value,
        limit=", ".join(parts),
        margin=margin,
        limit_source=limit.source_ref,
        message=(
            f"{feature.name}: {feature.value:g} {unit} "
            f"{'within' if ok else 'outside'} {', '.join(parts)} ({feature.basis})"
        ),
    )


class CheckManufacturabilityHandler(
    SkillBase[CheckManufacturabilityInput, CheckManufacturabilityOutput]
):
    """DFM check of a design's features against a process's sourced limits. No MCP tools."""

    input_type = CheckManufacturabilityInput
    output_type = CheckManufacturabilityOutput

    async def execute(
        self, input_data: CheckManufacturabilityInput
    ) -> CheckManufacturabilityOutput:
        self.logger.info(
            "Checking manufacturability",
            process=input_data.process,
            features=len(input_data.features),
            limits=len(input_data.limits),
        )
        findings: list[DfmFinding] = []
        for feature in input_data.features:
            limits = [lim for lim in input_data.limits if lim.kind == feature.kind]
            if not limits:
                findings.append(
                    _unknown(feature, f"no sourced {feature.kind} limit for {input_data.process}")
                )
                continue
            for limit in limits:
                check = _check_envelope if feature.kind == "envelope" else _check_value
                findings.append(check(feature, limit))
        failed = sum(1 for f in findings if f.status == "fail")
        unknown = sum(1 for f in findings if f.status == "unknown")
        passed = sum(1 for f in findings if f.status == "pass")
        if failed:
            verdict = "not_manufacturable"
        elif unknown or not findings:
            verdict = "incomplete"
        else:
            verdict = "manufacturable"
        unknowns = [f.message for f in findings if f.status == "unknown"]
        if not findings:
            unknowns.append("no features were given, so nothing was checked")
        return CheckManufacturabilityOutput(
            process=input_data.process,
            verdict=verdict,
            findings=findings,
            passed=passed,
            failed=failed,
            unknown=unknown,
            unknowns=unknowns,
        )
