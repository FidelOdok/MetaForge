"""Handler for the check_ergonomics skill (FORGE-633).

Compares human-contact features with limits for a user population. No
anthropometric figure is built in: each limit arrives with its population
and source, so the check is only as good as what was recorded, and says so.
A feature with no limit of its kind, or with no value, is ``unknown``; a
limit stated for a different population than the intended users is
reported, not silently applied as if it matched. Verdict: any fail is
``does_not_fit``; otherwise any unknown, or nothing checked, is
``incomplete``.
"""

from __future__ import annotations

from skill_registry.skill_base import SkillBase

from .schema import (
    UNITS,
    CheckErgonomicsInput,
    CheckErgonomicsOutput,
    ErgoFeature,
    ErgoFinding,
    ErgoLimit,
)


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _unknown(feature: ErgoFeature, why: str, limit: ErgoLimit | None = None) -> ErgoFinding:
    return ErgoFinding(
        feature=feature.name,
        kind=feature.kind,
        status="unknown",
        basis=feature.basis,
        value=feature.value,
        unit=UNITS[feature.kind],
        population=limit.population if limit else "",
        limit_source=limit.source_ref if limit else "",
        message=f"{feature.name}: {why}",
    )


def _check(feature: ErgoFeature, limit: ErgoLimit) -> ErgoFinding:
    if feature.value is None:
        return _unknown(feature, "no value given", limit)
    if limit.min is None and limit.max is None:
        return _unknown(feature, "the limit states neither min nor max", limit)
    unit = UNITS[feature.kind]
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
    return ErgoFinding(
        feature=feature.name,
        kind=feature.kind,
        status="pass" if ok else "fail",
        basis=feature.basis,
        value=feature.value,
        unit=unit,
        limit=", ".join(parts),
        margin=round(margin, 4),
        population=limit.population,
        limit_source=limit.source_ref,
        message=(
            f"{feature.name}: {feature.value:g} {unit} {'within' if ok else 'outside'} "
            f"{', '.join(parts)} for {limit.population} ({feature.basis})"
        ),
    )


class CheckErgonomicsHandler(SkillBase[CheckErgonomicsInput, CheckErgonomicsOutput]):
    """Ergonomic fit of a design's human-contact features. No MCP tools."""

    input_type = CheckErgonomicsInput
    output_type = CheckErgonomicsOutput

    async def execute(self, input_data: CheckErgonomicsInput) -> CheckErgonomicsOutput:
        self.logger.info(
            "Checking ergonomics",
            product=input_data.product,
            features=len(input_data.features),
            limits=len(input_data.limits),
        )
        findings: list[ErgoFinding] = []
        for feature in input_data.features:
            limits = [lim for lim in input_data.limits if lim.kind == feature.kind]
            if not limits:
                findings.append(
                    _unknown(feature, f"no sourced {feature.kind} limit for these users")
                )
                continue
            findings.extend(_check(feature, limit) for limit in limits)
        failed = sum(1 for f in findings if f.status == "fail")
        unknown = sum(1 for f in findings if f.status == "unknown")
        passed = sum(1 for f in findings if f.status == "pass")
        verdict = "does_not_fit" if failed else "incomplete" if unknown or not findings else "fits"
        unknowns = [f.message for f in findings if f.status == "unknown"]
        if not findings:
            unknowns.append("no features were given, so nothing was checked")
        intended = _norm(input_data.user_population)
        mismatches = sorted(
            {
                f"{lim.kind}: limit is for '{lim.population}', the users are "
                f"'{input_data.user_population}'"
                for lim in input_data.limits
                if _norm(lim.population) != intended
            }
        )
        return CheckErgonomicsOutput(
            product=input_data.product,
            user_population=input_data.user_population,
            verdict=verdict,
            findings=findings,
            passed=passed,
            failed=failed,
            unknown=unknown,
            unknowns=unknowns,
            population_mismatches=mismatches,
        )
