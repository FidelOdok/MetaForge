"""What kind of failure a phase hit, and what to do about it (FORGE-539).

A failed phase used to be one thing: the run ended with the error text. That
treats a network timeout, a missing API key, a malformed CAD file and a
design that fails its safety factor the same way, and they need opposite
responses: the first is worth retrying, the second needs a person to fix
configuration, the third needs the input repaired, and the last needs the
*design* changed, which no amount of retrying the same attempt will do.

So a failure is classified, and the class decides the response. The table
is deterministic on purpose: retry counters, backoff and "is this worth
retrying" are runtime guarantees, not judgement calls for a model.

Pure: no I/O. The Temporal workflow imports this to label a failed run.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "FailureClass",
    "FailureResponse",
    "FailureVerdict",
    "classify_failure",
    "response_for",
]


class FailureClass(StrEnum):
    #: Network blips, timeouts, a briefly unavailable service.
    TRANSIENT = "transient"
    #: A tool or adapter misbehaved (crashed, returned garbage).
    TOOL = "tool"
    #: The input was wrong: malformed geometry, a missing file, bad units.
    DATA = "data"
    #: No tool can do what was asked (no CFD solver, adapter not deployed).
    CAPABILITY = "capability"
    #: The design does not meet a requirement or constraint.
    DESIGN = "design"
    #: A required approval or permission is missing or was refused.
    AUTHORIZATION = "authorization"
    #: Missing configuration: no API key, no model, no worker.
    ENVIRONMENT = "environment"
    #: The model produced an unusable or ungrounded result.
    REASONING = "reasoning"
    UNKNOWN = "unknown"


class FailureResponse(StrEnum):
    RETRY = "retry"
    REPAIR_INPUT = "repair_input"
    USE_ALTERNATIVE_TOOL = "use_alternative_tool"
    REPLAN = "replan"
    REQUEST_INFORMATION = "request_information"
    REQUEST_APPROVAL = "request_approval"
    FIX_ENVIRONMENT = "fix_environment"
    FAIL = "fail"


#: The response each class calls for, and whether retrying the same attempt
#: can possibly help. Only a transient failure is retried as-is.
_POLICY: dict[FailureClass, tuple[FailureResponse, bool, str]] = {
    FailureClass.TRANSIENT: (
        FailureResponse.RETRY,
        True,
        "retry within the attempt budget; the same request may succeed",
    ),
    FailureClass.TOOL: (
        FailureResponse.USE_ALTERNATIVE_TOOL,
        False,
        "rebind the step to another tool that covers the capability, or report the tool",
    ),
    FailureClass.DATA: (
        FailureResponse.REPAIR_INPUT,
        False,
        "repair the input (file, units, parameters) before running the step again",
    ),
    FailureClass.CAPABILITY: (
        FailureResponse.REPLAN,
        False,
        "no tool can do this: replan around the gap (alternative method, defer, or ask)",
    ),
    FailureClass.DESIGN: (
        FailureResponse.REPLAN,
        False,
        "the design does not meet a requirement: rework the design phase, do not retry",
    ),
    FailureClass.AUTHORIZATION: (
        FailureResponse.REQUEST_APPROVAL,
        False,
        "a person must approve or grant access; retrying will not change the answer",
    ),
    FailureClass.ENVIRONMENT: (
        FailureResponse.FIX_ENVIRONMENT,
        False,
        "configuration is missing (key, model, worker); fix it, then start again",
    ),
    FailureClass.REASONING: (
        FailureResponse.RETRY,
        False,
        "re-run the phase with the findings as feedback (a gate retry), not a blind repeat",
    ),
    FailureClass.UNKNOWN: (
        FailureResponse.FAIL,
        False,
        "unclassified: report it with the error rather than guessing a remedy",
    ),
}


@dataclass(frozen=True)
class FailureVerdict:
    failure_class: FailureClass
    response: FailureResponse
    retryable: bool
    guidance: str

    def as_dict(self) -> dict[str, object]:
        return {
            "failure_class": self.failure_class.value,
            "response": self.response.value,
            "retryable": self.retryable,
            "guidance": self.guidance,
        }


def response_for(failure_class: FailureClass) -> FailureVerdict:
    response, retryable, guidance = _POLICY[failure_class]
    return FailureVerdict(failure_class, response, retryable, guidance)


#: Signals, checked in order. The first class with a matching signal wins, so
#: the more specific classes come first ("approval timed out" is about
#: authorization, not a transient timeout).
_SIGNALS: tuple[tuple[FailureClass, tuple[str, ...]], ...] = (
    (
        FailureClass.AUTHORIZATION,
        ("approval_required", "not approved", "permission", "forbidden", "unauthorized", "403"),
    ),
    (
        FailureClass.ENVIRONMENT,
        (
            "providerunavailable",
            "missing api key",
            "no api key",
            "no model provider",
            "not configured",
            "no worker",
            "workernotrunning",
        ),
    ),
    (
        FailureClass.CAPABILITY,
        (
            "unknown tool",
            "tool not found",
            "no such tool",
            "not supported",
            "unsupported",
            "no solver",
        ),
    ),
    (
        FailureClass.DESIGN,
        ("constraint violation", "safety factor", "exceeds", "requirement fail", "does not meet"),
    ),
    (
        FailureClass.DATA,
        (
            "file not found",
            "no such file",
            "invalid geometry",
            "malformed",
            "parse error",
            "invalid input",
            "validation error",
            "incompatible units",
            "unknown unit",
        ),
    ),
    (FailureClass.REASONING, ("ungrounded", "exhausted", "max_steps", "unparseable")),
    (
        FailureClass.TRANSIENT,
        (
            "timeout",
            "timed out",
            "temporarily",
            "connection reset",
            "connection refused",
            "503",
            "502",
            "429",
            "rate limit",
        ),
    ),
    (
        FailureClass.TOOL,
        (
            # MCP's adapter-unavailable code: the container is down, so another
            # tool covering the capability is the remedy (spec test 4).
            "-32001",
            "adapter unavailable",
            "traceback",
            "internal error",
            "crashed",
            "segmentation",
            "status 500",
        ),
    ),
)


def classify_failure(message: str, *, error_type: str = "") -> FailureVerdict:
    """The class of a failure from its message and, when known, its type name.

    Classifying by text is the honest option available: an activity error
    crosses a process boundary as a type name and a string. The signals are
    specific phrases, and anything that matches none is ``UNKNOWN`` rather
    than defaulted to "retry".
    """
    haystack = f"{error_type} {message}".lower()
    for failure_class, signals in _SIGNALS:
        if any(signal in haystack for signal in signals):
            return response_for(failure_class)
    return response_for(FailureClass.UNKNOWN)
