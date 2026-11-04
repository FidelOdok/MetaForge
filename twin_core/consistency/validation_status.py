"""One validation status vocabulary for every engineering check (FORGE-793).

FRS CHG-005: a check's status is exactly one of ``PASS``, ``WARN``, ``FAIL``,
``INCOMPLETE``, ``NOT_EVALUATED`` or ``STALE``, and lack of evidence is never
``PASS``. CHG-006: every check result names the input revisions it was run
against and the tool result it came from.

Several status fields already exist, each with its own words. This module does
not replace them; it maps each onto the shared vocabulary so a reader (the
impact view, a gate, the API) can compare them:

========================================  ==========================================
Existing field                            Mapping
========================================  ==========================================
``GateCheckStatus`` (gates)               pass, fail, not_evaluated: same names
``CheckStatus`` (cross-domain checks)     ``no_data`` is NOT_EVALUATED
requirement matrix ``status``             ``uncertain`` is WARN, ``no_data`` is
                                          NOT_EVALUATED, ``unverified`` is
                                          INCOMPLETE, ``stale`` is STALE
``ConstraintStatus``                      ``unevaluated`` is NOT_EVALUATED
a record's ``staleness``                  any status but current or revalidated
                                          is STALE, whatever the result said
========================================  ==========================================

The two "no answer" statuses differ on purpose: ``NOT_EVALUATED`` means no
check result exists; ``INCOMPLETE`` means a result exists but does not say
whether the check passed (a tool output with no verdict in it).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from twin_core.consistency.record_pins import record_pins, record_staleness
from twin_core.consistency.staleness import StalenessStatus


class ValidationStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    INCOMPLETE = "INCOMPLETE"
    NOT_EVALUATED = "NOT_EVALUATED"
    STALE = "STALE"


_SAME_NAME = {s.value.lower(): s for s in ValidationStatus}

#: Words the existing status fields use, mapped onto the shared vocabulary.
_ALIASES: dict[str, ValidationStatus] = {
    **_SAME_NAME,
    "passed": ValidationStatus.PASS,
    "ok": ValidationStatus.PASS,
    "warning": ValidationStatus.WARN,
    "uncertain": ValidationStatus.WARN,
    "failed": ValidationStatus.FAIL,
    "error": ValidationStatus.FAIL,
    "no_data": ValidationStatus.NOT_EVALUATED,
    "unevaluated": ValidationStatus.NOT_EVALUATED,
    "not_run": ValidationStatus.NOT_EVALUATED,
    "unverified": ValidationStatus.INCOMPLETE,
    "partial": ValidationStatus.INCOMPLETE,
    "invalid": ValidationStatus.STALE,
    "superseded": ValidationStatus.STALE,
}


def map_status(value: Any) -> ValidationStatus:
    """Map an existing status word (any of the fields above) to the shared one.

    An unknown word is ``NOT_EVALUATED``: a status nobody can read is not a pass.
    """
    raw = getattr(value, "value", value)
    return _ALIASES.get(str(raw or "").strip().lower(), ValidationStatus.NOT_EVALUATED)


def verdict_of_result(result: Any) -> ValidationStatus:
    """What a recorded tool result says about its check, read from its fields.

    Reads only explicit verdicts: a ``passed`` boolean, a ``status``/``verdict``
    word, or violation and warning counts (``violations``, ``errors``,
    ``warnings``, as KiCad's DRC/ERC and a build report them). A result with
    none of these is ``INCOMPLETE``: it exists but does not say.
    """
    if not isinstance(result, dict) or not result:
        return ValidationStatus.NOT_EVALUATED
    passed = result.get("passed", result.get("success"))
    if isinstance(passed, bool):
        if not passed:
            return ValidationStatus.FAIL
        return ValidationStatus.WARN if _count(result, "warnings") else ValidationStatus.PASS
    for key in ("status", "verdict", "outcome"):
        if isinstance(result.get(key), str) and result[key].strip():
            status = map_status(result[key])
            if status is not ValidationStatus.NOT_EVALUATED:
                return status
    counted = [k for k in ("violations", "errors", "error_count") if _has_count(result, k)]
    if counted:
        if any(_count(result, k) for k in counted):
            return ValidationStatus.FAIL
        return ValidationStatus.WARN if _count(result, "warnings") else ValidationStatus.PASS
    return ValidationStatus.INCOMPLETE


def _has_count(result: dict[str, Any], key: str) -> bool:
    value = result.get(key)
    return isinstance(value, (int, list)) and not isinstance(value, bool)


def _count(result: dict[str, Any], key: str) -> int:
    value = result.get(key)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, list):
        return len(value)
    return 0


class CheckResult(BaseModel):
    """One engineering check, bound to its inputs and its evidence (CHG-006)."""

    check: str
    status: ValidationStatus
    #: ``KEY@n`` refs of the revisions the check was run against.
    input_revisions: list[str] = Field(default_factory=list)
    #: The record holding the tool's actual output (a node id), if any.
    tool_result_ref: str | None = None
    tool: str | None = None
    detail: str = ""

    @model_validator(mode="after")
    def _no_pass_without_evidence(self) -> CheckResult:
        """CHG-005: a pass needs a tool result and the revisions it was for."""
        if self.status in (ValidationStatus.PASS, ValidationStatus.WARN) and (
            not self.tool_result_ref or not self.input_revisions
        ):
            missing = "tool result" if not self.tool_result_ref else "input revisions"
            self.detail = f"{self.detail + '; ' if self.detail else ''}not evaluated: no {missing}"
            self.status = ValidationStatus.NOT_EVALUATED
        return self


def check_result_for_record(
    record_id: str,
    metadata: dict[str, Any] | None,
    *,
    check: str,
    tool: str | None = None,
) -> CheckResult:
    """The shared-vocabulary status of one recorded check result.

    A record that is not current (``STALE``, ``SUPERSEDED``, ``INVALID``) is
    ``STALE`` whatever its result said: an old pass is history, not a pass.
    """
    meta = metadata or {}
    inputs = [str(p.get("item_ref") or "") for p in record_pins(meta) if p.get("item_ref")]
    staleness = record_staleness(meta)
    if staleness not in (StalenessStatus.CURRENT.value, StalenessStatus.REVALIDATED.value):
        return CheckResult(
            check=check,
            status=ValidationStatus.STALE,
            input_revisions=inputs,
            tool_result_ref=record_id,
            tool=tool,
            detail=str(meta.get("staleness_reason") or f"the result is {staleness}"),
        )
    result = meta.get("result") if isinstance(meta.get("result"), dict) else meta
    return CheckResult(
        check=check,
        status=verdict_of_result(result),
        input_revisions=inputs,
        tool_result_ref=record_id,
        tool=tool,
    )
