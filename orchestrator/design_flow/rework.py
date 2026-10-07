"""Sending a design-flow run back to an earlier phase (FORGE-500).

A retry (FORGE-495) re-runs the phase a gate belongs to. That cannot help when
the gate's verdict is about an *earlier* phase's work: a verification gate that
fails on a safety factor needs a design change, and the design phase is two
gates back. Before this the only way back was a new run from intent.

A fourth decision, *rework*, names an earlier phase of the same run. The run
jumps back to it, hands its brain the reviewer's reason, the findings of the
gate that sent it back and the failing phase's summary as its first input, then
re-runs that phase and every later one in order, opening each gate again.
Phases before the target keep their completed entries and their approvals.

Pure helpers only: both engines (the Temporal workflow and the in-process
executor) share them, and the workflow imports this module, so it must stay
free of I/O and clocks.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from orchestrator.design_flow.rework_context import RevisionNote, revision_note_lines

__all__ = [
    "STALL_STOP_ENV",
    "DEFAULT_STALL_STOP",
    "findings_streak",
    "stall_note",
    "stall_stop",
    "DEFAULT_MAX_REWORK_CYCLES",
    "MAX_REWORK_CYCLES_ENV",
    "build_rework_feedback",
    "max_rework_cycles",
    "rework_target_error",
]

#: How many times one run may be sent back. Bounded so a design that cannot
#: satisfy its gates ends the run instead of looping on a human.
DEFAULT_MAX_REWORK_CYCLES = 3

MAX_REWORK_CYCLES_ENV = "METAFORGE_DESIGN_FLOW_MAX_REWORK_CYCLES"

#: The failing phase's summary is context, not the payload; keep the prompt small.
_SUMMARY_LIMIT = 1500


#: FORGE-573: identical gate findings this many times in a row end the run.
#: Retries and reworks are capped, but a repair that changes nothing used
#: every one of them before stopping; the same verdict twice is the signal.
DEFAULT_STALL_STOP = 3
STALL_STOP_ENV = "METAFORGE_DESIGN_FLOW_STALL_STOP"


def stall_stop() -> int:
    """How many identical not-ready verdicts in a row end the run (min 2)."""
    raw = (os.environ.get(STALL_STOP_ENV) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_STALL_STOP
    return max(value, 2)


def findings_streak(history: Sequence[Sequence[str]]) -> int:
    """How many of the latest not-ready verdicts in a row had the same findings.

    ``history`` is one phase's findings, one entry per not-ready gate, oldest
    first (cleared when the gate passes). Order within an entry does not
    matter. 0 for an empty history, 1 when the latest differs from the one
    before: the attempt changed something.
    """
    if not history:
        return 0
    last = frozenset(history[-1])
    streak = 0
    for entry in reversed(history):
        if frozenset(entry) != last:
            break
        streak += 1
    return streak


def stall_note(phase_id: str, streak: int, stop_at: int) -> str:
    """The line a reviewer reads when attempts stop improving."""
    if streak >= stop_at:
        return (
            f"Phase '{phase_id}' stopped: its last {streak} attempts drew the same gate "
            "findings, so repairing it the same way is not converging. Change the approach "
            "(requirements, concept or material) and start a new run."
        )
    return (
        f"NO IMPROVEMENT: the same findings as the previous attempt ({streak} in a row; "
        f"the run stops at {stop_at}). Change the approach, rework an earlier phase, or reject"
    )


def max_rework_cycles() -> int:
    """The configured per-run rework cap (env override, else the default)."""
    raw = (os.environ.get(MAX_REWORK_CYCLES_ENV) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_REWORK_CYCLES
    return max(value, 0)


def rework_target_error(phase_ids: Sequence[str], current: str | None, to_phase: str) -> str | None:
    """Why ``to_phase`` is not a valid rework target, or ``None`` when it is.

    Only an *earlier* phase of this run's own frozen flow qualifies. Naming the
    current phase is a retry, and a later phase has no work to redo yet.
    """
    if not to_phase.strip():
        return "rework needs 'to_phase': the id of an earlier phase of this run"
    if to_phase not in phase_ids:
        return f"phase '{to_phase}' is not part of this run's flow (phases: {', '.join(phase_ids)})"
    if current is None or current not in phase_ids:
        return None
    if phase_ids.index(to_phase) >= phase_ids.index(current):
        earlier = list(phase_ids[: phase_ids.index(current)])
        return (
            f"phase '{to_phase}' is not earlier than the current phase '{current}'; "
            f"rework targets one of: {', '.join(earlier) or 'none (this is the first phase)'}. "
            "Use retry to re-run the current phase"
        )
    return None


def build_rework_feedback(
    *,
    from_phase: str,
    to_phase: str,
    findings: Sequence[str],
    reason: str,
    from_summary: str,
    cycle: int,
    revisions: Sequence[RevisionNote] = (),
) -> str:
    """The block handed to the target phase's brain as the first thing in its prompt."""
    lines = [
        f"REWORK (cycle {cycle}): the gate after phase '{from_phase}' sent the run back to "
        f"'{to_phase}'. Phases before '{to_phase}' are approved and stay as they are; redo "
        f"'{to_phase}' and the phases after it will run again."
    ]
    if findings:
        lines.append(f"Findings of the '{from_phase}' gate:")
        lines.extend(f"  - {f}" for f in findings)
    summary = from_summary.strip()
    if summary:
        if len(summary) > _SUMMARY_LIMIT:
            summary = summary[:_SUMMARY_LIMIT] + "..."
        lines.append(f"Summary of phase '{from_phase}':")
        lines.append(summary)
    if reason.strip():
        lines.append(f"Reviewer's reason: {reason.strip()}")
    # FORGE-525: drafts of the gate that sent the run back were closed; earlier
    # approved phases' revisions are current and are what this phase revises.
    lines.append(
        f"What '{from_phase}' recorded in the twin since the last approved gate was "
        f"discarded; '{to_phase}' revises the current, approved revisions."
    )
    lines.extend(revision_note_lines(revisions))  # FORGE-530: rejected refs, reasons, diffs
    lines.append("Change the work so these problems are resolved before you reply.")
    return "\n".join(lines)
