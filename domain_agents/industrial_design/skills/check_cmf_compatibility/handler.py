"""Handler for the check_cmf_compatibility skill (FORGE-633).

DFM-aware aesthetics: an appearance is only a valid choice for a part whose
material it was recorded as suitable for. The check uses what the library
says (the appearance's ``materials``), not a built-in table of which
finishes suit which materials, so a missing record is ``unknown`` rather
than a guess. Material names compare case-insensitively, with aluminium and
aluminum as one spelling, as the material resolver does.
"""

from __future__ import annotations

from skill_registry.skill_base import SkillBase

from .schema import (
    AppearanceRecord,
    CheckCmfCompatibilityInput,
    CheckCmfCompatibilityOutput,
    CmfFinding,
    PartCmf,
    Status,
)


def _key(name: str) -> str:
    return name.strip().lower().replace("aluminum", "aluminium")


def _finding(part: PartCmf, status: Status, message: str) -> CmfFinding:
    return CmfFinding(
        part=part.name,
        appearance=part.appearance,
        material=part.material,
        status=status,
        message=f"{part.name}: {message}",
    )


def _review(appearance: AppearanceRecord) -> list[str]:
    out = []
    if appearance.colour.get("needs_review"):
        out.append(f"{appearance.name}: colour")
    for key, prop in sorted(appearance.properties.items()):
        if prop.get("needs_review"):
            out.append(f"{appearance.name}: {key}")
    return out


class CheckCmfCompatibilityHandler(
    SkillBase[CheckCmfCompatibilityInput, CheckCmfCompatibilityOutput]
):
    """Appearance against material, per part. No MCP tools."""

    input_type = CheckCmfCompatibilityInput
    output_type = CheckCmfCompatibilityOutput

    async def execute(self, input_data: CheckCmfCompatibilityInput) -> CheckCmfCompatibilityOutput:
        self.logger.info("Checking CMF compatibility", parts=len(input_data.parts))
        records = {_key(a.name): a for a in input_data.appearances}
        findings: list[CmfFinding] = []
        used: dict[str, AppearanceRecord] = {}
        for part in input_data.parts:
            if not part.appearance:
                findings.append(_finding(part, "unknown", "no appearance chosen"))
                continue
            record = records.get(_key(part.appearance))
            if record is None:
                findings.append(
                    _finding(
                        part,
                        "unknown",
                        f"appearance {part.appearance} was not given (read it with "
                        "library.get_appearance)",
                    )
                )
                continue
            used[record.name] = record
            if not part.material:
                findings.append(_finding(part, "unknown", "no material stated for the part"))
            elif not record.materials:
                findings.append(
                    _finding(
                        part,
                        "unknown",
                        f"{record.name} does not record which materials it suits",
                    )
                )
            elif _key(part.material) in {_key(m) for m in record.materials}:
                findings.append(
                    _finding(
                        part,
                        "pass",
                        f"{record.name} ({record.finish or 'finish not stated'}) is recorded "
                        f"for {part.material}",
                    )
                )
            else:
                findings.append(
                    _finding(
                        part,
                        "fail",
                        f"{record.name} is recorded for {', '.join(record.materials)}, "
                        f"not {part.material}",
                    )
                )
        failed = sum(1 for f in findings if f.status == "fail")
        unknown = sum(1 for f in findings if f.status == "unknown")
        passed = sum(1 for f in findings if f.status == "pass")
        verdict = "incompatible" if failed else "incomplete" if unknown else "compatible"
        processes = sorted({p for a in used.values() for p in a.processes})
        review = [item for a in used.values() for item in _review(a)]
        return CheckCmfCompatibilityOutput(
            verdict=verdict,
            findings=findings,
            passed=passed,
            failed=failed,
            unknown=unknown,
            finishing_processes=processes,
            needs_review=review,
        )
