"""What earlier runs on a project learned, for the kickoff and concept briefs.

The Chair run's concept had no joints. An earlier run on the same project had
already been sent back for exactly that, and nothing carried the finding
forward: every run started from the goal alone. This module turns earlier
runs' records into a short, deduplicated list of lessons that the kickoff and
concept phases are briefed with.

Three sources, read by the caller and handed in as plain data, so this stays
pure and the two engines share it:

- **gate rejections**: the reason on every reject, retry or rework a person
  or the policy logged on an earlier run (``request["decisions"]``);
- **reviewer findings**: the reasons and concerns a gate reviewer wrote on an
  earlier run (``request["gate_reviews"]``), skipping approvals' reasons,
  which say why something passed, not what went wrong;
- **recorded lessons**: lessons already written down, such as an earlier
  kickoff record's ``lessons`` and the twin's rejected revisions.

Newest first, deduplicated on the normalised text, capped at
:data:`MAX_PRIOR_LESSONS` so the brief stays short.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "LESSON_CHARS",
    "MAX_PRIOR_LESSONS",
    "PriorLesson",
    "lessons_from_runs",
    "merge_lessons",
    "render_lessons",
]

#: The most lessons a brief carries.
MAX_PRIOR_LESSONS = 8
#: Characters of one lesson kept; the rest is cut with an ellipsis.
LESSON_CHARS = 240

#: Decisions that turned a gate down, so their reason is a lesson.
_TURNED_DOWN = frozenset({"reject", "retry", "rework"})


@dataclass(frozen=True)
class PriorLesson:
    text: str
    #: The run it came from, when known.
    source_run: str = ""
    #: ``gate_rejection`` | ``reviewer_finding`` | ``recorded``.
    kind: str = "recorded"

    def as_metadata(self) -> dict[str, str]:
        return {"text": self.text, "source_run": self.source_run}


def _clip(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= LESSON_CHARS else text[: LESSON_CHARS - 3] + "..."


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _when(record: Mapping[str, Any]) -> float:
    try:
        return float(record.get("updated_at") or record.get("created_at") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _run_lessons(run_id: str, request: Mapping[str, Any]) -> list[PriorLesson]:
    out: list[PriorLesson] = []
    for raw in reversed(list(request.get("gate_reviews") or [])):
        if not isinstance(raw, Mapping):
            continue
        phase = str(raw.get("phase") or "").strip()
        label = f"{phase}: " if phase else ""
        if str(raw.get("recommendation") or "") in _TURNED_DOWN:
            reasons = str(raw.get("reasons") or "").strip()
            if reasons:
                out.append(PriorLesson(_clip(label + reasons), run_id, "reviewer_finding"))
        for concern in raw.get("concerns") or []:
            if isinstance(concern, str) and concern.strip():
                out.append(PriorLesson(_clip(label + concern), run_id, "reviewer_finding"))
    for raw in reversed(list(request.get("decisions") or [])):
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("decision") or "") not in _TURNED_DOWN:
            continue
        reason = str(raw.get("reason") or "").strip()
        if reason:
            verb = {"reject": "rejected", "retry": "retried", "rework": "reworked"}[
                str(raw.get("decision"))
            ]
            out.append(PriorLesson(_clip(f"a gate was {verb}: {reason}"), run_id, "gate_rejection"))
    return out


def lessons_from_runs(
    runs: Iterable[Mapping[str, Any]],
    *,
    project_id: str | None,
    exclude_run_id: str | None = None,
) -> list[PriorLesson]:
    """The gate rejections and reviewer findings of a project's earlier runs.

    ``runs`` are run records (``{"id", "request", "updated_at"}``, as the run
    store and its ledger keep them). Runs of another project, and the run
    asking, are left out. Newest run first; not yet deduplicated or capped
    (:func:`merge_lessons` does both).
    """
    if not project_id:
        return []
    mine = [
        r
        for r in runs
        if isinstance(r.get("request"), Mapping)
        and str(r["request"].get("project_id") or "") == str(project_id)
        and str(r.get("id") or "") != str(exclude_run_id or "")
    ]
    mine.sort(key=_when, reverse=True)
    out: list[PriorLesson] = []
    for record in mine:
        out.extend(_run_lessons(str(record.get("id") or ""), record["request"]))
    return out


def merge_lessons(
    *groups: Iterable[PriorLesson], limit: int = MAX_PRIOR_LESSONS
) -> list[PriorLesson]:
    """The groups in order, each lesson once (by its normalised text), capped."""
    seen: set[str] = set()
    out: list[PriorLesson] = []
    for group in groups:
        for lesson in group:
            key = _key(lesson.text)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(lesson)
            if len(out) >= limit:
                return out
    return out


def render_lessons(lessons: list[PriorLesson], heading: str) -> str:
    """A brief block, or ``""`` when there are none (so a first run adds nothing)."""
    if not lessons:
        return ""
    lines = [heading]
    for lesson in lessons:
        source = f" (run {lesson.source_run})" if lesson.source_run else ""
        lines.append(f"  - {lesson.text}{source}")
    return "\n".join(lines)
