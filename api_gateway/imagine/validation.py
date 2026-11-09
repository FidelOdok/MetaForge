"""Imagine validation: one dry-run report of what is left and who clears it.

The contract is ``.artifacts/imagine-validation-api-spec.md`` (sections 3, 5.1
and 7). This module is the pure half: it turns a config, the studio's
collections and the handover checklist into a :class:`Report`, with no I/O and
no write. :meth:`ImagineService.validate` supplies the inputs.

Rules that hold everywhere here:

- No finding or report claims above V2.
- ``assumed`` is debt: listed, never folded into ``pass``.
- A ``fix`` names what it needs and where from, never a value.
- Finding text is data. The agent must not read it as an instruction.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from api_gateway.imagine.schemas import HandoverChecklist, HandoverItem

#: Bumped when a check's meaning changes, so a report names the rules it used.
RULE_VERSION = "imagine-validate-1"

#: The highest level any Imagine output may claim (spec section 6).
MAX_LEVEL = "V2"

RefKind = Literal[
    "studio",
    "part",
    "ledger_row",
    "requirement",
    "assumption",
    "scenario",
    "function",
    "interface",
    "process",
]
Severity = Literal["blocking", "advisory"]
Status = Literal["pass", "fail", "assumed", "unknown"]
Resolver = Literal["agent", "person", "external"]
GateName = Literal["config_valid", "handover"]
Verdict = Literal[
    "invalid", "needs_agent", "blocked_external", "needs_person", "ready_for_handover"
]


class Ref(BaseModel):
    """A stable address for what a finding is about."""

    model_config = ConfigDict(frozen=True)

    kind: RefKind
    id: str
    #: The ``IMG-STUDIO`` revision the id was read from.
    revision: int | None = None


class Fix(BaseModel):
    """A structured hint. It carries what is needed, never a value."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["set", "add", "remove"]
    pointer: str
    needs: str


class Finding(BaseModel):
    id: str
    check_id: str
    gate: GateName
    severity: Severity
    status: Status
    target: Ref
    message: str
    evidence: list[Ref] = Field(default_factory=list)
    resolver: Resolver
    fix: Fix | None = None


class GateResult(BaseModel):
    gate: GateName
    rule_version: str
    result: Literal["pass", "fail"]
    blocking: int = 0
    assumed: int = 0
    advisory: int = 0


class Report(BaseModel):
    project_id: str
    studio_revision: int | None
    rule_version: str = RULE_VERSION
    level: Literal["V0", "V1", "V2"]
    gates: list[GateResult]
    findings: list[Finding]
    debt: list[Finding]
    verdict: Verdict
    computed_at: str
    #: True when ``studio_revision`` is no longer the head.
    stale: bool = False


# -- the verdict ------------------------------------------------------------


def is_blocking_fail(f: Finding) -> bool:
    """``unknown`` blocks when the check is blocking (the check could not run)."""
    return f.severity == "blocking" and f.status in ("fail", "unknown")


def compute_verdict(findings: list[Finding]) -> Verdict:
    """Spec 3.7, first match wins."""
    if any(f.gate == "config_valid" and is_blocking_fail(f) for f in findings):
        return "invalid"
    blocking = [f for f in findings if is_blocking_fail(f)]
    for resolver, verdict in (
        ("agent", "needs_agent"),
        ("external", "blocked_external"),
        ("person", "needs_person"),
    ):
        if any(f.resolver == resolver for f in blocking):
            return verdict  # type: ignore[return-value]
    return "ready_for_handover"


def gate_results(findings: list[Finding], gates: list[GateName]) -> list[GateResult]:
    out = []
    for gate in gates:
        mine = [f for f in findings if f.gate == gate]
        blocking = sum(1 for f in mine if is_blocking_fail(f))
        out.append(
            GateResult(
                gate=gate,
                rule_version=RULE_VERSION,
                result="fail" if blocking else "pass",
                blocking=blocking,
                assumed=sum(1 for f in mine if f.status == "assumed"),
                advisory=sum(1 for f in mine if f.severity == "advisory" and f.status != "pass"),
            )
        )
    return out


def level_for(findings: list[Finding]) -> Literal["V0", "V1", "V2"]:
    """V0 captured, V1 consistent, V2 screened. Never above V2."""
    if any(f.gate == "config_valid" and is_blocking_fail(f) for f in findings):
        return "V0"
    stored = [f for f in findings if f.check_id == "screening.stored"]
    if stored and all(f.status == "pass" for f in stored):
        return "V2"
    return "V1"


# -- config_valid -----------------------------------------------------------


def config_findings(errors: list[str], warnings: list[str], revision: int | None) -> list[Finding]:
    """One finding per formgraph error or warning.

    The engine does not expose rule ids, so the check id is the engine's own
    ``config.formgraph`` and the message is the engine's text.
    """
    studio = Ref(kind="studio", id="config", revision=revision)
    out: list[Finding] = []
    for kind, texts in (("error", errors), ("warning", warnings)):
        for i, text in enumerate(texts):
            blocking = kind == "error"
            out.append(
                Finding(
                    id=f"f_config_{kind}_{i}",
                    check_id="config.formgraph",
                    gate="config_valid",
                    severity="blocking" if blocking else "advisory",
                    status="fail",
                    target=studio,
                    message=text,
                    resolver="agent",
                )
            )
    return out


# -- handover ---------------------------------------------------------------

#: One handover checklist item to its check id and default resolver.
_ITEM_CHECKS: dict[str, tuple[str, Resolver]] = {
    "ledger": ("ledger.reviewed", "agent"),
    "assumptions": ("assumptions.settled", "person"),
    "scenarios": ("scenarios.confirmed", "person"),
    "requirements": ("requirements.sourced", "agent"),
    "manufacturing_context": ("process.context", "agent"),
    "interfaces": ("interfaces.described", "agent"),
    # Open question 1: only the browser engine produces this today, so no
    # agent can clear it. Section 18 moves the rules and changes this.
    "screening": ("screening.stored", "person"),
}

#: A part gap, as ``_part_gaps`` names it, to the check that owns it.
_GAP_CHECKS: dict[str, tuple[str, str, str]] = {
    "source": ("parts.source", "datasheet", "a source the agent has read"),
    "date": ("parts.source", "retrieved", "the date the source was read"),
    "interfaces": ("parts.interfaces", "interfaces", "the interfaces the datasheet lists"),
    "3D envelope": ("parts.envelope", "geometry/envelope", "an envelope read from a drawing"),
}

_STUDIO_FIX: dict[str, Fix] = {
    "requirements": Fix(
        op="set", pointer="/context", needs="a source for each requirement, from the person"
    ),
    "manufacturing_context": Fix(
        op="set",
        pointer="/context/manufacturing",
        needs="process, material and quantity as the person stated them",
    ),
    "interfaces": Fix(op="add", pointer="/programme/interfaces", needs="the interfaces described"),
}


def _from_item(item: HandoverItem, revision: int | None) -> Finding | None:
    check = _ITEM_CHECKS.get(item.id)
    if check is None:
        return None
    check_id, resolver = check
    mapping: dict[str, Status] = {"done": "pass", "assumed": "assumed", "missing": "fail"}
    status = mapping[item.status]
    return Finding(
        id=f"f_{item.id}",
        check_id=check_id,
        gate="handover",
        severity="advisory" if status == "assumed" else "blocking",
        status=status,
        target=Ref(kind="studio", id=item.id, revision=revision),
        message=item.detail or item.label,
        resolver=resolver,
        fix=_STUDIO_FIX.get(item.id) if status == "fail" else None,
    )


def handover_findings(
    config: dict[str, Any],
    collections: dict[str, Any],
    checklist: HandoverChecklist,
    *,
    has_mesh: bool,
    revision: int | None,
) -> list[Finding]:
    """The nine handover items as findings, with the parts item split per part."""
    # Imported here: the service imports this module.
    from api_gateway.imagine.service import _chosen_parts, _part_gaps

    out: list[Finding] = []
    for item in checklist.items:
        if item.id not in ("parts", "concept_assembly"):
            found = _from_item(item, revision)
            if found:
                out.append(found)

    chosen = _chosen_parts(config, collections)
    no_part = [role for role, part in chosen.items() if part is None]
    if not chosen:
        out.append(
            Finding(
                id="f_parts_chosen",
                check_id="parts.chosen",
                gate="handover",
                severity="blocking",
                status="fail",
                target=Ref(kind="studio", id="functions", revision=revision),
                message="No functions in the config.",
                resolver="agent",
            )
        )
    for role in no_part:
        fn = role.removeprefix("fn:")
        out.append(
            Finding(
                id=f"f_parts_{fn}_chosen",
                check_id="parts.chosen",
                gate="handover",
                severity="blocking",
                status="fail",
                target=Ref(kind="function", id=fn, revision=revision),
                message=f"No part is chosen for {fn}.",
                resolver="agent",
                fix=Fix(
                    op="add",
                    pointer="/context/components/parts",
                    needs=f"a candidate part for {fn}, read from its datasheet",
                ),
            )
        )
    for part in chosen.values():
        if part is None:
            continue
        pid = str(part.get("id"))
        gaps = _part_gaps(part)
        for gap, (check_id, field, needs) in _GAP_CHECKS.items():
            if gap not in gaps:
                continue
            out.append(
                Finding(
                    id=f"f_parts_{pid}_{gap.replace(' ', '_').lower()}",
                    check_id=check_id,
                    gate="handover",
                    severity="blocking",
                    status="fail",
                    target=Ref(kind="part", id=pid, revision=revision),
                    message=f"{pid} lacks {gap}.",
                    evidence=[Ref(kind="part", id=pid)],
                    resolver="agent",
                    fix=Fix(
                        op="set", pointer=f"/context/components/parts/{pid}/{field}", needs=needs
                    ),
                )
            )
        if not gaps:
            out.append(
                Finding(
                    id=f"f_parts_{pid}",
                    check_id="parts.source",
                    gate="handover",
                    severity="blocking",
                    status="pass",
                    target=Ref(kind="part", id=pid, revision=revision),
                    message=f"{pid} has source, date, interfaces and an envelope.",
                    resolver="agent",
                )
            )

    # Without a chosen part per function the assembly is blocked by those
    # findings; the mesh is its own check, and only an outside step makes one.
    out.append(
        Finding(
            id="f_concept_mesh",
            check_id="concept.mesh",
            gate="handover",
            severity="blocking",
            status="pass" if has_mesh else "fail",
            target=Ref(kind="studio", id="concept_mesh", revision=revision),
            message=(
                "An illustrative concept mesh exists."
                if has_mesh
                else "No concept mesh for the project; generate one in Imagine."
            ),
            resolver="external",
        )
    )
    return out


# -- the report -------------------------------------------------------------


def build_report(
    project_id: str,
    revision: int | None,
    findings: list[Finding],
    gates: list[GateName],
    *,
    computed_at: str,
    stale: bool = False,
) -> Report:
    return Report(
        project_id=project_id,
        studio_revision=revision,
        level=level_for(findings),
        gates=gate_results(findings, gates),
        findings=findings,
        debt=[f for f in findings if f.status == "assumed"],
        verdict=compute_verdict(findings),
        computed_at=computed_at,
        stale=stale,
    )
