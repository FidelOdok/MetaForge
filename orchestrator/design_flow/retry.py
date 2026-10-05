"""Retrying a design-flow phase from its gate (FORGE-495).

A gate used to answer approve or reject, and a reject, or a gate that was not
ready, ended the run. Recovery meant a whole new run from intent with every
earlier gate approved again, to fix one missing deliverable. A third decision,
*retry*, re-runs the same phase with the gate's findings and the reviewer's
reason as the first thing the phase brain reads, and opens the same gate again.

Pure helpers only: both engines (the Temporal workflow and the in-process
executor) share them, and the workflow imports this module, so it must stay
free of I/O and clocks.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

__all__ = [
    "DEFAULT_MAX_PHASE_RETRIES",
    "MAX_PHASE_RETRIES_ENV",
    "build_retry_feedback",
    "max_phase_retries",
]

#: How many times one phase may be re-run from its gate. Bounded so a phase
#: that cannot satisfy its gate ends the run instead of looping on a human.
DEFAULT_MAX_PHASE_RETRIES = 3

MAX_PHASE_RETRIES_ENV = "METAFORGE_DESIGN_FLOW_MAX_PHASE_RETRIES"


def max_phase_retries() -> int:
    """The configured per-phase retry cap (env override, else the default)."""
    raw = (os.environ.get(MAX_PHASE_RETRIES_ENV) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_PHASE_RETRIES
    return max(value, 0)


def build_retry_feedback(*, findings: Sequence[str], reason: str, attempt: int) -> str:
    """The block handed to the phase brain as the first thing in its prompt."""
    lines = [
        f"RETRY (attempt {attempt}): your previous attempt at this phase did not pass "
        "its gate. Earlier phases are approved and stay as they are; redo only this one."
    ]
    if findings:
        lines.append("Gate findings:")
        lines.extend(f"  - {f}" for f in findings)
    if reason.strip():
        lines.append(f"Reviewer's reason: {reason.strip()}")
    # FORGE-525: the previous attempt's twin writes were drafts and were closed,
    # so nothing it recorded is current. Saying so stops a phase "reusing" them.
    lines.append(
        "What the previous attempt recorded in the twin was discarded (it never became "
        "current); record this attempt's work again."
    )
    lines.append("Fix exactly these problems before you reply.")
    return "\n".join(lines)
