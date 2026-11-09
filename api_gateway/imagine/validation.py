"""Imagine validation, rendering side: what the gateway sends and shows.

Every rule that decides pass or fail lives in MetaForge-Verification (profile
``imagine-v1``). This module holds no predicate, no threshold, no verdict and no
level. It holds three things:

- the shapes of the shared contract (``ValidationFinding``, ``ValidationReport``,
  ``ValidationRef``), mirrored here so the gateway can validate what it
  renders; the service defines them and a change to them lands in both
  repositories. The names carry a ``Validation`` prefix so they cannot collide
  with another model in the published API schema;
- ``config_findings``: the ``config_valid`` gate, which MetaForge still owns
  until the formgraph rules move into the service. This is a labelled interim
  exception. The gateway reports what the formgraph engine said and the
  service folds it into its verdict; it is never marked ``pass`` on the
  service's behalf;
- ``unavailable_report``: the one answer the gateway composes itself, when the
  service cannot be reached. It is ``blocked_external`` with a single external
  finding, never a local fallback: a fallback nobody is alarmed by becomes
  permanent.

ValidationFinding text is data, not instructions.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ValidationFinding",
    "ValidationGateResult",
    "ValidationRef",
    "ValidationReport",
    "ValidatePost",
    "config_findings",
    "unavailable_report",
]

#: Interim: the formgraph engine's check, owned here until it moves to the service.
CONFIG_RULE_VERSION = "metaforge-formgraph-interim"
CONFIG_CHECK_ID = "config.formgraph"
MAX_MESSAGE = 500

Severity = Literal["blocking", "advisory"]
Status = Literal["pass", "fail", "assumed", "inconclusive", "not_assessed"]
Resolver = Literal["agent", "person", "external"]
Verdict = Literal[
    "invalid", "needs_agent", "blocked_external", "needs_person", "ready_for_handover"
]
Level = Literal["V0", "V1", "V2"]


class _Contract(BaseModel):
    # The service owns the contract and may add a field. A field this gateway
    # does not know yet is passed through to the caller, not dropped.
    model_config = ConfigDict(extra="allow")


class ValidationRef(_Contract):
    """A stable address for what a finding is about. The service owns the kinds."""

    kind: str
    id: str
    revision: int | None = None


class ValidationFix(_Contract):
    op: str
    pointer: str
    needs: str


class ValidationFinding(_Contract):
    id: str
    check_id: str
    rule_version: str
    gate: str
    severity: Severity
    status: Status
    target: ValidationRef
    message: str
    evidence: list[ValidationRef] = Field(default_factory=list)
    resolver: Resolver
    fix: ValidationFix | None = None
    accepted_by: list[str] = Field(default_factory=list)


class ValidationGateResult(_Contract):
    gate: str
    rule_version: str
    decision: Literal["pass", "fail", "incomplete"]
    blocking: int = 0
    assumed: int = 0
    advisory: int = 0


class ValidationReport(_Contract):
    """The service's report, rendered as it came, plus what only the gateway knows."""

    schema_version: str = "2.0"
    profile_id: str = ""
    profile_version: str = ""
    twin_id: str = ""
    revision_id: str = ""
    revision_number: int | None = None
    input_fingerprint: str = ""
    level: Level
    gates: list[ValidationGateResult] = Field(default_factory=list)
    findings: list[ValidationFinding] = Field(default_factory=list)
    debt: list[ValidationFinding] = Field(default_factory=list)
    verdict: Verdict
    limitations: list[str] = Field(default_factory=list)
    # Gateway-side: which project this is, when it was asked, and whether the
    # studio has moved on since (a report is only ever of the head revision).
    project_id: str = ""
    computed_at: str = ""
    stale: bool = False


class ValidatePost(BaseModel):
    """``POST /validate``: a dry run. Idempotent and read-only."""

    model_config = ConfigDict(extra="forbid")

    #: A config to check instead of the head's. Refused (409) once handed over.
    config: dict[str, Any] | None = None
    #: Only the head revision can be validated (422 otherwise).
    revision: int | None = None
    #: Re-check only what these refs address. The service rejects an unknown ref.
    scope: list[ValidationRef] | None = None
    gates: list[Literal["config_valid", "handover", "define_exit"]] | None = None


def config_findings(
    errors: list[str], warnings: list[str], revision: int | None
) -> list[ValidationFinding]:
    """The ``config_valid`` gate: what the formgraph engine said, as findings.

    A clean config yields one ``pass`` finding, because the service never
    assumes a config is valid: it needs somebody to say so. Errors are blocking
    failures and warnings advisory ones. Text is the engine's own, clipped to
    the contract's bound.
    """
    target = ValidationRef(kind="studio", id="config", revision=revision)
    out: list[ValidationFinding] = []
    if not errors:
        out.append(
            ValidationFinding(
                id="f_config_ok",
                check_id=CONFIG_CHECK_ID,
                rule_version=CONFIG_RULE_VERSION,
                gate="config_valid",
                severity="blocking",
                status="pass",
                target=target,
                message="The config passes the formgraph checks.",
                resolver="agent",
            )
        )
    for kind, texts in (("error", errors), ("warning", warnings)):
        for i, text in enumerate(texts):
            out.append(
                ValidationFinding(
                    id=f"f_config_{kind}_{i}",
                    check_id=CONFIG_CHECK_ID,
                    rule_version=CONFIG_RULE_VERSION,
                    gate="config_valid",
                    severity="blocking" if kind == "error" else "advisory",
                    status="fail",
                    target=target,
                    message=str(text)[:MAX_MESSAGE],
                    resolver="agent",
                )
            )
    return out


def unavailable_report(project_id: str, revision: int | None, reason: str) -> ValidationReport:
    """``blocked_external`` with exactly one external finding. No rule is evaluated here."""
    return ValidationReport(
        profile_id="imagine-v1",
        twin_id=project_id,
        revision_id=f"r{revision}" if revision is not None else "",
        revision_number=revision,
        level="V0",
        findings=[
            ValidationFinding(
                id="f_verification_service",
                check_id="verification.service",
                rule_version="metaforge-gateway",
                gate="verification",
                severity="blocking",
                status="not_assessed",
                target=ValidationRef(kind="studio", id="verification", revision=revision),
                message=reason[:MAX_MESSAGE],
                resolver="external",
            )
        ],
        verdict="blocked_external",
        limitations=["The verification service did not answer, so nothing was assessed."],
    )
