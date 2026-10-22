"""Deterministic task checks at stage gates (composable flows, story 6).

A fragment task declares acceptance checks over design values
(``max_junction_temp_c <= max_allowed_junction_temp_c``). They are evaluated
when the stage that reviews the task reaches its gate, by both engines, and
the answer is one of three:

* PASS: the recorded value satisfies the bound;
* FAIL: it does not -- a blocking finding, never something the approver can
  wave through by approving;
* NO_DATA: the value, or the limit it is compared to, has not been recorded
  (or is not a number). Planner §22: a gate is a policy over current state,
  and "no data" is a gap, not a pass. Also blocking.

A block phase (:mod:`orchestrator.design_flow.coupling`) adds one more check:
its coupled values converged.

Pure functions over plain data. Whoever calls this reads the design values;
nothing here touches the twin.
"""

from __future__ import annotations

import math
import operator
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from orchestrator.design_flow.coupling import judge
from orchestrator.design_flow.spec import Coupling

__all__ = [
    "CheckOutcome",
    "CheckResult",
    "DesignValueReader",
    "StageCheckReport",
    "attached_tasks",
    "evaluate_stage",
    "history_names",
    "value_names",
]


class CheckOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NO_DATA = "NO_DATA"


_OPS: dict[str, Callable[[float, float], bool]] = {
    "<=": operator.le,
    "<": operator.lt,
    ">=": operator.ge,
    ">": operator.gt,
    "==": operator.eq,
    "!=": operator.ne,
}


@runtime_checkable
class DesignValueReader(Protocol):
    """Reads a project's recorded design values (``design_value`` items).

    ``values`` maps each value's name to its current number; ``history`` maps
    each requested name to every recorded revision, oldest first (what a
    coupled block's iterations left behind).
    """

    async def values(self, project_id: str | None) -> Mapping[str, Any]: ...

    async def history(
        self, project_id: str | None, names: Sequence[str]
    ) -> Mapping[str, Sequence[Any]]: ...


@dataclass(frozen=True)
class CheckResult:
    task: str
    check: str
    describe: str
    outcome: CheckOutcome
    value: float | None = None
    bound: float | None = None
    reason: str = ""

    @property
    def blocking(self) -> bool:
        return self.outcome is not CheckOutcome.PASS

    def finding(self) -> str:
        if self.outcome is CheckOutcome.FAIL:
            return (
                f"task '{self.task}' check '{self.check}' failed: {self.describe} "
                f"(recorded {self.value:g}, bound {self.bound:g})"
            )
        return f"task '{self.task}' check '{self.check}' has no data: {self.reason}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "check": self.check,
            "describe": self.describe,
            "outcome": self.outcome.value,
            "value": self.value,
            "bound": self.bound,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class StageCheckReport:
    """Every check of the tasks one stage gate reviews."""

    stage: str
    results: tuple[CheckResult, ...] = ()
    #: False when nothing could be read (no reader wired, the read failed).
    #: With checks declared, an unread report blocks: it is no_data for all.
    evaluated: bool = True
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def findings(self) -> list[str]:
        return [*self.notes, *(r.finding() for r in self.results if r.blocking)]

    @property
    def blocking(self) -> bool:
        return bool(self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "evaluated": self.evaluated,
            "results": [r.as_dict() for r in self.results],
            "notes": list(self.notes),
        }


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def attached_tasks(phases: Iterable[Any], stage_id: str) -> list[Any]:
    """The tasks a stage's gate reviews, in flow order. Works on frozen phases too."""
    return [p for p in phases if _field(p, "fragment") and _field(p, "stage") == stage_id]


def value_names(tasks: Iterable[Any]) -> list[str]:
    """Every design value the tasks' checks read (values and limits)."""
    names: list[str] = []
    for task in tasks:
        for check in _field(task, "checks", ()) or ():
            names.append(str(_field(check, "value")))
            if _field(check, "limit"):
                names.append(str(_field(check, "limit")))
    return list(dict.fromkeys(names))


def history_names(tasks: Iterable[Any]) -> list[str]:
    names: list[str] = []
    for task in tasks:
        coupling = _field(task, "coupling")
        if coupling:
            names.extend(str(v) for v in _field(coupling, "solves_for", ()) or ())
    return list(dict.fromkeys(names))


def _number(raw: Any) -> float | None:
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _describe(check: Any) -> str:
    describe = getattr(check, "describe", None)
    if callable(describe):
        return str(describe())
    limit = _field(check, "limit")
    if limit:
        return f"{_field(check, 'value')} {_field(check, 'op')} {limit}"
    unit = f" {_field(check, 'unit')}" if _field(check, "unit") else ""
    threshold = float(_field(check, "threshold") or 0.0)
    return f"{_field(check, 'value')} {_field(check, 'op')} {threshold:g}{unit}"


def _one(task_id: str, check: Any, values: Mapping[str, Any]) -> CheckResult:
    name = str(_field(check, "value"))
    op = str(_field(check, "op"))
    limit = str(_field(check, "limit") or "")
    describe = _describe(check)
    cid = str(_field(check, "id") or name)
    if name not in values:
        return CheckResult(
            task_id, cid, describe, CheckOutcome.NO_DATA, reason=f"'{name}' was never recorded"
        )
    value = _number(values[name])
    if value is None:
        return CheckResult(
            task_id,
            cid,
            describe,
            CheckOutcome.NO_DATA,
            reason=f"'{name}' is recorded as {values[name]!r}, not a number",
        )
    if limit:
        if limit not in values:
            return CheckResult(
                task_id,
                cid,
                describe,
                CheckOutcome.NO_DATA,
                value=value,
                reason=f"limit '{limit}' was never recorded (it must come, with its source, "
                "from the requirements or a datasheet)",
            )
        bound = _number(values[limit])
        if bound is None:
            return CheckResult(
                task_id,
                cid,
                describe,
                CheckOutcome.NO_DATA,
                value=value,
                reason=f"limit '{limit}' is recorded as {values[limit]!r}, not a number",
            )
    else:
        bound = float(_field(check, "threshold") or 0.0)
    compare = _OPS.get(op)
    if compare is None:
        return CheckResult(task_id, cid, describe, CheckOutcome.NO_DATA, reason=f"unknown op {op}")
    outcome = CheckOutcome.PASS if compare(value, bound) else CheckOutcome.FAIL
    return CheckResult(task_id, cid, describe, outcome, value=value, bound=bound)


def _coupling_of(task: Any) -> Coupling | None:
    raw = _field(task, "coupling")
    if not raw:
        return None
    return Coupling(
        members=tuple(_field(raw, "members", ()) or ()),
        solves_for=tuple(_field(raw, "solves_for", ()) or ()),
        tolerance=float(_field(raw, "tolerance", 0.01)),
        max_iterations=int(_field(raw, "max_iterations", 20)),
    )


def _convergence(
    task_id: str, coupling: Coupling, history: Mapping[str, Sequence[Any]]
) -> CheckResult:
    names = coupling.solves_for
    series = {
        n: [v for v in (_number(x) for x in history.get(n, ())) if v is not None] for n in names
    }
    depth = min((len(s) for s in series.values()), default=0)
    passes = [{n: series[n][len(series[n]) - depth + i] for n in names} for i in range(depth)]
    verdict = judge(passes, coupling)
    describe = f"{', '.join(names)} converge within {coupling.tolerance:.2%}"
    if verdict.converged:
        return CheckResult(task_id, "converged", describe, CheckOutcome.PASS)
    outcome = CheckOutcome.NO_DATA if verdict.status.value == "NO_DATA" else CheckOutcome.FAIL
    worst = max(verdict.changes.values(), default=0.0) if verdict.changes else None
    return CheckResult(
        task_id,
        "converged",
        describe,
        outcome,
        value=worst,
        bound=coupling.tolerance if worst is not None else None,
        reason=verdict.reason,
    )


def evaluate_stage(
    stage_id: str,
    phases: Iterable[Any],
    values: Mapping[str, Any] | None,
    history: Mapping[str, Sequence[Any]] | None = None,
) -> StageCheckReport:
    """Evaluate every check of the tasks ``stage_id``'s gate reviews.

    ``values`` ``None`` means they could not be read: every declared check is
    NO_DATA and the report says why, rather than reading as clean.
    """
    tasks = attached_tasks(phases, stage_id)
    declared = any(_field(t, "checks") or _field(t, "coupling") for t in tasks)
    if not declared:
        return StageCheckReport(stage=stage_id)
    if values is None:
        return StageCheckReport(
            stage=stage_id,
            evaluated=False,
            notes=(
                f"the checks of {len(tasks)} task(s) under '{stage_id}' could not be evaluated: "
                "no design values could be read, and no data is not a pass",
            ),
        )
    results: list[CheckResult] = []
    for task in tasks:
        tid = str(_field(task, "id"))
        for check in _field(task, "checks", ()) or ():
            results.append(_one(tid, check, values))
        coupling = _coupling_of(task)
        if coupling is not None:
            results.append(_convergence(tid, coupling, history or {}))
    return StageCheckReport(stage=stage_id, results=tuple(results))
