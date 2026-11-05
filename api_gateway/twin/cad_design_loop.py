"""The self-validating CAD design loop: write CadQuery, build, look, critique, revise.

A model writes (or edits) a CadQuery script for one component, the script is
built and checked, the result is rendered in a real headless browser and a
vision critic compares the pictures and the measured numbers with the intent
and the manufacturing (DFM) context. The model revises until it is satisfied
or a cap is reached. One run is one background job (``twin.design_cad_loop``).

**Each iteration**

a. *Context* (once per run): the intent (the brief, the component, the
   references with their roles and facts, stated dimensions, the
   ``IFC-CAD-EDA`` interface contract's parameters, the envelope), the
   component's parametric plan (``PSK``) and proposed feature history
   (``FHP``), the manufacturing context block (stated, assumed and unknown)
   and the component's current DFM findings.
b. *Write*: the author model returns a whole script, seeded from the
   parametric plan when its inputs are complete. A dimension marked unknown
   is never invented: the model asks for it, or (when working values are
   allowed) uses one it labels as an assumption, recorded and shown to the
   person. A reply that does neither for a required unknown stops the run
   with a question.
c. *Build and check*: ``cadquery.execute_script``, ``cadquery.validate_shape``
   and, under a profile with exact checks (FDM), ``cadquery.dfm_measure`` and
   the profile's exact checks; otherwise the profile's heuristic rules on the
   measured extents. Every call goes through MCP; the script runs only inside
   the CadQuery adapter container.
d. *Render*: STEP to GLB through the OCCT converter, then
   ``render.model_views`` in the render adapter (headless Chromium).
e. *Critique*: the vision critic gets the contact sheet, the measured numbers,
   the intent and the DFM findings, and returns structured issues (severity,
   view, feature anchor, suggested change) and a ``satisfied`` verdict.
f. *Stop or revise*: the run stops ``satisfied`` only when the critic is
   satisfied, validity is ``pass``, no blocking DFM finding remains and every
   stated overall dimension is within tolerance. It stops at the iteration
   cap or the spend cap with its open issues, ``needs_input`` on a question
   for a person, or ``failed`` when a model or adapter could not be reached.

**Records.** A run is one ``cad_design_loop`` work product: each iteration's
script, build result, validity, measurements, rendered views (named blobs),
critique, DFM findings, model, provider and cost, and the manufacturing
context revision. The result is a *proposed* CAD revision bound to the base
revision the run started from. Nothing is applied: a person accepts it
(``Use this version``), which builds it through the exact CAD path (the head
moves only on a validity pass), or keeps the previous one. The critic's
verdict is never evidence: it never sets ``validated`` or ``verified``
(3D-RC-012).

**Spend.** Every model call counts against the run's budget: a number of
calls and, where the provider prices them, a USD limit. Unpriced calls count
as calls. The route checks the project's hourly cap for the tool first, as
every paid step does.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field, ValidationError, field_validator

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.cad_design_loop")

LOOP_TOOL = "twin.design_cad_loop"
META_KEY = "cad_design_loop"
TARGET_PREFIX = "cad-loop:"
EXPECTED_SECONDS = 300.0
DEFAULT_MAX_ITERATIONS = 5
MAX_ITERATIONS_LIMIT = 8
DEFAULT_TOLERANCE_MM = 0.5
SPEND_ENV = "METAFORGE_CAD_LOOP_MAX_SPEND_USD"
DEFAULT_MAX_SPEND_USD = 1.0
MODEL_TIMEOUT_ENV = "METAFORGE_CAD_LOOP_MODEL_TIMEOUT_S"
DEFAULT_MODEL_TIMEOUT_S = 120.0
DEFAULT_VIEWS: tuple[str, ...] = ("iso", "front", "side", "top")
MAX_SCRIPT_CHARS = 20_000
IFC_CAD_EDA = "IFC-CAD-EDA"
#: Feature plan parameters that are not dimensions (the manufacturing
#: context answers them), so never asked for as one.
_NON_DIMENSIONS = frozenset({"material"})


class StopReason(StrEnum):
    SATISFIED = "satisfied"
    ITERATION_CAP = "iteration_cap"
    SPEND_CAP = "spend_cap"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"


#: What each stop reason says to a person, before the details.
STOP_WORDS: dict[str, str] = {
    StopReason.SATISFIED: (
        "The critic is satisfied, the geometry check passed, no blocking manufacturing "
        "finding remains and the stated dimensions are within tolerance"
    ),
    StopReason.ITERATION_CAP: "Stopped at the iteration limit with issues still open",
    StopReason.SPEND_CAP: "Stopped at the spend limit with issues still open",
    StopReason.NEEDS_INPUT: "Waiting for your answer before it can go on",
    StopReason.FAILED: "Could not go on",
}


class CadLoopError(ValueError):
    """A request the loop refuses. ``status`` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


class LoopUnavailableError(RuntimeError):
    """A model or an adapter the loop needs could not be reached."""


@dataclass(frozen=True)
class ModelReply:
    """A model's answer: the text, who answered and what it cost (``None``: unknown)."""

    text: str
    provider: str | None = None
    model: str | None = None
    cost_usd: float | None = None


#: ``prompt -> reply``: the author writing CadQuery.
AuthorCall = Callable[[str], Awaitable[ModelReply]]
#: ``(png, mime, prompt) -> reply``: the vision critic.
CriticCall = Callable[[bytes, str, str], Awaitable[ModelReply]]
#: ``step -> glb``, through the OCCT converter.
GlbConverter = Callable[[bytes], bytes]
#: ``(node_id, filename, content, content_type) -> object key``.
BlobWriter = Callable[[str, str, bytes, str], str | None]
Publisher = Callable[[str, dict[str, Any]], Awaitable[None]]

_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-gateway")
    return _metrics


def _metric(name: str, *args: Any) -> None:
    try:
        getattr(_collector(), name)(*args)
    except Exception as exc:  # noqa: BLE001 - a counter never fails a run
        logger.debug("cad_loop_metric_failed", metric=name, error=str(exc))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _pid(project_id: str | UUID) -> UUID:
    try:
        return project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    except ValueError as exc:
        raise CadLoopError(f"project id {project_id!r} is not a UUID", 400) from exc


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return value if value > 0 else default


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------


class CadLoopRequest(BaseModel):
    """Refine one component's CAD automatically."""

    #: The component's parametric plan (``PSK-...``); or name the component.
    sketch_key: str | None = Field(None, max_length=80)
    component_id: str | None = Field(None, max_length=200)
    #: Anything the person adds for this run, in their words.
    intent: str | None = Field(None, max_length=2000)
    #: Overall extents the result must have, mm (CAD axes).
    target_dimensions: dict[str, float] | None = None
    tolerance_mm: float | None = Field(None, gt=0, le=10)
    #: Answers to questions an earlier run asked, by the question's name.
    answers: dict[str, float | str] = Field(default_factory=dict, max_length=50)
    #: The model may use a labelled working value for an unknown dimension.
    allow_working_values: bool = True
    max_iterations: int = Field(DEFAULT_MAX_ITERATIONS, ge=1, le=MAX_ITERATIONS_LIMIT)
    max_model_calls: int | None = Field(None, ge=1, le=40)
    max_spend_usd: float | None = Field(None, gt=0, le=50)
    views: list[str] | None = Field(None, max_length=6)

    @field_validator("target_dimensions")
    @classmethod
    def _axes(cls, value: dict[str, float] | None) -> dict[str, float] | None:
        if value is None:
            return None
        out: dict[str, float] = {}
        for axis, mm in value.items():
            key = axis.strip().lower()
            if key not in ("x", "y", "z"):
                raise ValueError("target_dimensions takes x, y and z in mm")
            if not mm > 0:
                raise ValueError("a target dimension must be above zero")
            out[key] = float(mm)
        return out or None


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass
class Unknown:
    """A dimension nobody has stated. Never invented."""

    name: str
    question: str
    source: str
    #: Needed to build the component (a chosen feature's input).
    required: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "question": self.question,
            "source": self.source,
            "required": self.required,
        }


@dataclass
class Stated:
    """A dimension someone stated, with where it came from."""

    name: str
    value: Any
    unit: str
    source: str
    tolerance: float | None = None
    basis: str = "stated"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "source": self.source,
            "tolerance": self.tolerance,
            "basis": self.basis,
        }


@dataclass
class LoopContext:
    """Everything one run reads, gathered once."""

    project_id: str
    component_id: str
    component_name: str
    sketch_key: str | None
    sketch_ref: str | None
    cad_key: str
    base_revision: str | None
    feature_plan: list[dict[str, Any]] = field(default_factory=list)
    feature_history: list[dict[str, Any]] = field(default_factory=list)
    feature_history_ref: str | None = None
    seed_script: str | None = None
    seed_note: str = ""
    brief: dict[str, str] = field(default_factory=dict)
    intent: str = ""
    references: list[dict[str, Any]] = field(default_factory=list)
    stated: list[Stated] = field(default_factory=list)
    unknowns: list[Unknown] = field(default_factory=list)
    contract: dict[str, Any] | None = None
    envelope: dict[str, float] | None = None
    targets: dict[str, float] = field(default_factory=dict)
    tolerance_mm: float = DEFAULT_TOLERANCE_MM
    manufacturing_text: str = ""
    manufacturing_ref: str = "unresolved"
    manufacturing_unresolved: list[str] = field(default_factory=list)
    process: Any = None
    profile: Any = None
    stored_context: Any = None
    dfm_current: dict[str, Any] | None = None
    views: list[str] = field(default_factory=lambda: list(DEFAULT_VIEWS))

    def summary(self) -> dict[str, Any]:
        """What the record keeps of the context (no prompt text)."""
        return {
            "component_id": self.component_id,
            "component_name": self.component_name,
            "sketch_ref": self.sketch_ref,
            "cad_key": self.cad_key,
            "base_revision": self.base_revision,
            "feature_history_ref": self.feature_history_ref,
            "seeded_from_plan": self.seed_script is not None,
            "seed_note": self.seed_note,
            "references": [
                {k: r.get(k) for k in ("reference_id", "role", "parameter", "value", "unit")}
                for r in self.references[:20]
            ],
            "stated_dimensions": [s.as_dict() for s in self.stated],
            "unknown_dimensions": [u.as_dict() for u in self.unknowns],
            "interface_contract": self.contract,
            "envelope_mm": self.envelope,
            "target_dimensions_mm": self.targets or None,
            "tolerance_mm": self.tolerance_mm,
            "manufacturing_context_ref": self.manufacturing_ref,
            "manufacturing_unresolved": list(self.manufacturing_unresolved),
            "process": getattr(self.process, "process", None),
            "dfm_profile": getattr(self.profile, "ref", None),
            "dfm_status_before": (self.dfm_current or {}).get("status"),
            "views": list(self.views),
        }


def _envelope_of(value: Any) -> dict[str, float] | None:
    """``{x, y, z}`` mm from a stated envelope (dict or three numbers), else ``None``."""
    if isinstance(value, list | tuple) and len(value) == 3:
        nums = [_num(v) for v in value]
        if all(n is not None and n > 0 for n in nums):
            return {"x": nums[0], "y": nums[1], "z": nums[2]}  # type: ignore[dict-item]
        return None
    if isinstance(value, dict):
        aliases = {"x": ("x", "width", "length"), "y": ("y", "depth"), "z": ("z", "height")}
        out: dict[str, float] = {}
        for axis, names in aliases.items():
            for name in names:
                n = _num(value.get(name))
                if n is not None and n > 0:
                    out[axis] = n
                    break
        return out or None
    return None


# ---------------------------------------------------------------------------
# Prompts and replies
# ---------------------------------------------------------------------------


class AuthorAssumption(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    value: Any = None
    unit: str = Field("mm", max_length=20)
    why: str = Field("", max_length=400)


class AuthorQuestion(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=400)


class AuthorReply(BaseModel):
    """What the author model returns."""

    status: str = "script"
    script: str = Field("", max_length=MAX_SCRIPT_CHARS)
    summary: str = Field("", max_length=400)
    focus: str = Field("", max_length=120)
    assumptions: list[AuthorAssumption] = Field(default_factory=list, max_length=30)
    questions: list[AuthorQuestion] = Field(default_factory=list, max_length=20)


class CritiqueIssue(BaseModel):
    severity: str = "minor"
    view: str | None = Field(None, max_length=40)
    feature: str | None = Field(None, max_length=200)
    description: str = Field("", max_length=600)
    suggested_change: str = Field("", max_length=600)

    @field_validator("severity")
    @classmethod
    def _sev(cls, value: str) -> str:
        v = (value or "").strip().lower()
        return v if v in ("blocking", "major", "minor") else "minor"


class Critique(BaseModel):
    satisfied: bool = False
    reasons: list[str] = Field(default_factory=list, max_length=20)
    issues: list[CritiqueIssue] = Field(default_factory=list, max_length=30)


def _json_object(text: str) -> dict[str, Any]:
    """The first JSON object in a model's reply (fenced or bare)."""
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("the reply has no JSON object")
    value = json.loads(raw[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("the reply is not a JSON object")
    return value


def parse_author(text: str) -> AuthorReply:
    try:
        reply = AuthorReply.model_validate(_json_object(text))
    except (ValueError, ValidationError) as exc:
        raise ValueError(f"the model's reply could not be read: {str(exc)[:200]}") from exc
    reply.status = "question" if reply.status.strip().lower() == "question" else "script"
    return reply


def parse_critique(text: str) -> Critique:
    try:
        return Critique.model_validate(_json_object(text))
    except (ValueError, ValidationError) as exc:
        raise ValueError(f"the critic's reply could not be read: {str(exc)[:200]}") from exc


def _lines(title: str, rows: list[str]) -> str:
    return f"{title}:\n" + ("\n".join(f"- {r}" for r in rows) if rows else "- none")


def _fmt_value(value: Any, unit: str) -> str:
    shown = f"{value:g}" if isinstance(value, float) else str(value)
    return f"{shown} {unit}".strip()


def _dfm_lines(dfm: dict[str, Any] | None) -> list[str]:
    if not dfm:
        return []
    out = []
    for f in dfm.get("findings") or []:
        anchor = f.get("anchor") or {}
        where = anchor.get("feature") or ", ".join((anchor.get("faces") or [])[:3]) or "part"
        out.append(
            f"[{f.get('severity')}] {f.get('rule_title') or f.get('rule_id')}: measured "
            f"{f.get('measured')} {f.get('unit') or ''} against {f.get('comparison') or ''} "
            f"{f.get('limit')} at {where}. Fix: {f.get('suggested_fix') or ''}"
        )
    for n in dfm.get("not_assessed") or []:
        out.append(f"[not assessed] {n.get('rule_id') or ''}: {n.get('text') or n.get('reason')}")
    return out[:30]


def _profile_lines(profile: Any) -> list[str]:
    if profile is None:
        return []
    out = []
    for rule in getattr(profile, "rules", [])[:20]:
        limit = f" {rule.check} {rule.limit} {rule.unit}" if rule.limit is not None else ""
        out.append(f"{rule.title} ({rule.kind}){limit}")
    return out


def author_prompt(
    ctx: LoopContext,
    *,
    iteration: int,
    max_iterations: int,
    previous: dict[str, Any] | None,
    allow_working_values: bool,
) -> str:
    """The author model's prompt: intent, context, the last attempt and its review."""
    stated = [
        f"{s.name} = {_fmt_value(s.value, s.unit)}"
        + (f" (tolerance {s.tolerance})" if s.tolerance is not None else "")
        + f" [{s.basis}, {s.source}]"
        for s in ctx.stated
    ]
    unknown = [
        f"{u.name}: {u.question} [{u.source}{', required' if u.required else ''}]"
        for u in ctx.unknowns
    ]
    plan = [
        f"{f.get('id')} {f.get('op')}: "
        + ", ".join(
            f"{name}={p.get('value') if p.get('status') == 'provided' else 'UNKNOWN'}"
            + (f" {p.get('unit')}" if p.get("unit") else "")
            for name, p in (f.get("parameters") or {}).items()
        )
        for f in ctx.feature_plan
    ]
    history = [
        f"{f.get('name') or f.get('id')} ({f.get('kind') or f.get('op') or ''})"
        for f in ctx.feature_history[:20]
    ]
    refs = [
        f"{r.get('role')} reference: {r.get('parameter') or r.get('text') or ''} "
        f"{r.get('value') if r.get('value') is not None else ''} {r.get('unit') or ''}".strip()
        for r in ctx.references[:20]
    ]
    brief = [f"{k}: {v}" for k, v in ctx.brief.items() if v][:12]
    contract = []
    if ctx.contract:
        for p in ctx.contract.get("parameters") or []:
            value = p.get("value") if p.get("basis") != "unknown" else "UNKNOWN"
            contract.append(
                f"{p.get('label') or p.get('name')}: {value} {p.get('unit') or ''} "
                f"[{p.get('basis')}, owner {p.get('owner')}]"
            )
    working = (
        "You may use a working value for an UNKNOWN dimension only if you list it in "
        '"assumptions" with the same name, a value and why; a person will see it.'
        if allow_working_values
        else "Working values are not allowed in this run: for any UNKNOWN dimension you "
        "need, ask a question instead of writing a script."
    )
    parts = [
        "You are a mechanical CAD engineer writing a CadQuery script for one component. "
        "The script is built in a sandbox, rendered and reviewed, and you revise it.",
        f"Component: {ctx.component_name} ({ctx.component_id}). Iteration {iteration} of "
        f"{max_iterations}.",
        _lines("Brief", brief),
        f"What the person asked for this run: {ctx.intent}" if ctx.intent else "",
        _lines("Stated dimensions (keep these exactly)", stated),
        _lines("UNKNOWN dimensions (never invent these)", unknown),
        _lines("Parametric plan", plan),
        _lines("Proposed feature history", history),
        _lines("References", refs),
        _lines(f"Interface contract {IFC_CAD_EDA}", contract) if ctx.contract else "",
        (
            "Envelope (mm): "
            + ", ".join(f"{k.upper()} {v:g}" for k, v in (ctx.envelope or {}).items())
            if ctx.envelope
            else ""
        ),
        (
            "Target overall size (mm, within "
            + f"{ctx.tolerance_mm:g} mm): "
            + ", ".join(f"{k.upper()} {v:g}" for k, v in ctx.targets.items())
            if ctx.targets
            else ""
        ),
        "Manufacturing context:\n" + (ctx.manufacturing_text or "unresolved"),
        _lines(
            f"Process rules ({getattr(ctx.profile, 'ref', 'no profile')})",
            _profile_lines(ctx.profile),
        ),
        _lines("Current DFM findings for this component", _dfm_lines(ctx.dfm_current)),
    ]
    if previous is None and ctx.seed_script:
        parts.append(
            "Start from this script, compiled from the parametric plan:\n```python\n"
            + ctx.seed_script
            + "\n```"
        )
    if previous is not None:
        parts.append(_previous_block(previous))
    parts.append(
        "Rules: millimetres; import only cadquery (as cq) and math; assign the final "
        "cq.Workplane to a variable named result; one solid unless the plan says "
        "otherwise; keep stated dimensions exactly; address every blocking DFM finding "
        "and every critic issue, or say in summary why not. "
        + working
        + "\nReply with ONE JSON object and nothing else:\n"
        '{"status": "script" or "question", "script": "<the whole script>", '
        '"summary": "<one sentence: what this version changes>", '
        '"focus": "<a few words, e.g. fixing wall thickness>", '
        '"assumptions": [{"name": "...", "value": 0, "unit": "mm", "why": "..."}], '
        '"questions": [{"name": "...", "question": "..."}]}'
    )
    return "\n\n".join(p for p in parts if p)


def _previous_block(prev: dict[str, Any]) -> str:
    lines = [f"Your previous script (iteration {prev.get('n')}):\n```python\n"]
    lines.append(str(prev.get("script") or "") + "\n```")
    build = prev.get("build") or {}
    if build.get("error"):
        lines.append(f"It did not build: {build['error']}")
    validity = prev.get("validity") or {}
    if validity.get("outcome") and validity.get("outcome") != "pass":
        bad = [
            f"{f.get('check')}: {f.get('detail')}"
            for f in validity.get("findings") or []
            if f.get("outcome") != "pass"
        ]
        lines.append(_lines(f"Validity {validity.get('outcome')}", bad))
    measures = prev.get("measurements") or {}
    if measures:
        lines.append("Measured: " + json.dumps(measures, sort_keys=True)[:800])
    lines.append(_lines("DFM findings", _dfm_lines(prev.get("dfm"))))
    dims = prev.get("dimension_checks") or []
    off = [
        f"{d['axis'].upper()} measured {d['measured']:g} mm, wanted {d['wanted']}"
        for d in dims
        if not d.get("ok")
    ]
    lines.append(_lines("Dimensions out of tolerance", off))
    critique = prev.get("critique") or {}
    issues = [
        f"[{i.get('severity')}] {i.get('view') or ''} {i.get('feature') or ''}: "
        f"{i.get('description')} Suggested: {i.get('suggested_change')}"
        for i in critique.get("issues") or []
    ]
    lines.append(_lines("Critic issues", issues))
    return "\n".join(lines)


def critic_prompt(ctx: LoopContext, record: dict[str, Any]) -> str:
    """The vision critic's prompt; the image is the contact sheet of the views."""
    stated = [f"{s.name} = {_fmt_value(s.value, s.unit)} [{s.basis}]" for s in ctx.stated]
    measures = record.get("measurements") or {}
    parts = [
        "You are reviewing a CAD model of one component against its intent. The image is "
        "a sheet of rendered views (labelled; flat views are orthographic with a scale bar "
        "and measured dimensions). Judge only what the views and numbers show. Your "
        "verdict is a review, not a measurement or a validation.",
        f"Component: {ctx.component_name}. What this version changed: "
        f"{record.get('summary') or 'first version'}",
        _lines("Brief", [f"{k}: {v}" for k, v in ctx.brief.items() if v][:12]),
        f"Asked for this run: {ctx.intent}" if ctx.intent else "",
        _lines("Stated dimensions", stated),
        _lines(
            "Unknown dimensions (labelled working values may stand in)",
            [u.name for u in ctx.unknowns],
        ),
        "Measured: " + json.dumps(measures, sort_keys=True)[:1200],
        _lines("DFM findings", _dfm_lines(record.get("dfm"))),
        "Manufacturing context:\n" + (ctx.manufacturing_text or "unresolved")[:1500],
        "Reply with ONE JSON object and nothing else:\n"
        '{"satisfied": true or false, "reasons": ["..."], "issues": [{"severity": '
        '"blocking" or "major" or "minor", "view": "iso|front|side|top|...", "feature": '
        '"<where, e.g. left wall>", "description": "...", "suggested_change": "..."}]}\n'
        "Say satisfied only when the model matches the intent and you see no blocking "
        "or major issue.",
    ]
    return "\n\n".join(p for p in parts if p)


def unknowns_unanswered(
    ctx: LoopContext, reply: AuthorReply, *, allow_working_values: bool
) -> list[dict[str, str]]:
    """The questions a reply leaves for a person: nothing unknown is ever invented.

    A required unknown the model neither asked about nor labelled as a working
    value becomes a question; with working values not allowed, a labelled one
    becomes a question too.
    """

    def key(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")

    asked = {key(q.name) for q in reply.questions}
    labelled = {key(a.name) for a in reply.assumptions}
    out: list[dict[str, str]] = [
        {"name": q.name, "question": q.question, "source": "model"} for q in reply.questions
    ]
    for u in ctx.unknowns:
        k = key(u.name)
        if k in asked:
            continue
        if k in labelled and allow_working_values:
            continue
        if k in labelled or u.required:
            out.append({"name": u.name, "question": u.question, "source": u.source})
    return out


def dimension_checks(ctx: LoopContext, bbox: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Measured overall extents against the targets (exact) and the envelope (maximum)."""
    rows: list[dict[str, Any]] = []
    if not bbox:
        return rows
    size = {
        a: _num(bbox.get(f"max_{a}")) - _num(bbox.get(f"min_{a}"))  # type: ignore[operator]
        for a in ("x", "y", "z")
        if _num(bbox.get(f"max_{a}")) is not None and _num(bbox.get(f"min_{a}")) is not None
    }
    tol = ctx.tolerance_mm
    for axis, wanted in ctx.targets.items():
        if axis in size:
            ok = abs(size[axis] - wanted) <= tol
            rows.append(
                {
                    "axis": axis,
                    "kind": "target",
                    "wanted": f"{wanted:g} mm ± {tol:g}",
                    "measured": round(size[axis], 4),
                    "ok": ok,
                }
            )
    for axis, limit in (ctx.envelope or {}).items():
        if axis in size:
            rows.append(
                {
                    "axis": axis,
                    "kind": "envelope",
                    "wanted": f"at most {limit:g} mm",
                    "measured": round(size[axis], 4),
                    "ok": size[axis] <= limit + tol,
                }
            )
    return rows


def _bbox_size(bbox: dict[str, Any] | None) -> dict[str, float] | None:
    if not bbox:
        return None
    try:
        return {
            a: round(float(bbox[f"max_{a}"]) - float(bbox[f"min_{a}"]), 4) for a in ("x", "y", "z")
        }
    except (KeyError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# The run's budget
# ---------------------------------------------------------------------------


@dataclass
class Budget:
    """Model calls and priced spend a run may use."""

    max_calls: int
    max_usd: float
    calls: int = 0
    usd: float = 0.0
    unpriced: int = 0

    def refusal(self) -> str | None:
        """Why another call would exceed the budget, else ``None``."""
        if self.calls >= self.max_calls:
            return f"the run's {self.max_calls} model calls are used"
        if self.usd >= self.max_usd:
            return f"the run's ${self.max_usd:.2f} model spend is used"
        return None

    def add(self, cost: float | None) -> None:
        self.calls += 1
        if cost is None:
            self.unpriced += 1
        else:
            self.usd += float(cost)

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_model_calls": self.max_calls,
            "max_spend_usd": self.max_usd,
            "model_calls": self.calls,
            "spend_usd": round(self.usd, 6),
            "unpriced_calls": self.unpriced,
        }


# ---------------------------------------------------------------------------
# Default model calls (the gateway's provider chain)
# ---------------------------------------------------------------------------


def _spent(run_key: str) -> float | None:
    try:
        from orchestrator.harness.providers.usage import run_usage

        totals = run_usage(run_key)
    except Exception:  # noqa: BLE001 - no accounting reads as unknown
        return None
    value = (totals or {}).get("cost_usd")
    return float(value) if isinstance(value, int | float) else None


def make_default_author_call() -> AuthorCall:
    """A text call over the chat provider chain, priced from the usage store."""

    async def call(prompt: str) -> ModelReply:
        from orchestrator.harness.providers import (
            AllProvidersFailedError,
            ProviderPipeline,
            RoleModelSlots,
            default_invoke,
        )
        from orchestrator.harness.providers.usage import current_usage_context

        try:
            from api_gateway.chat.harness_backend import provider_config_from_env

            config = provider_config_from_env()
            candidates = list(config.slots.candidates("generator"))
        except Exception as exc:  # noqa: BLE001 - reported as unavailable
            raise LoopUnavailableError(f"no model provider is configured: {exc}") from exc
        if not candidates:
            raise LoopUnavailableError("no model provider is configured")
        answered: dict[str, str] = {}

        async def _invoke(spec: Any, _request: Any) -> Any:
            request = {
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 6000,
                "temperature": 0.2,
            }
            result = await default_invoke(spec, request)
            answered.update(provider=spec.name, model=spec.model)
            return result

        run_key = current_usage_context().run_id or ""
        before = _spent(run_key) if run_key else None
        pipeline = ProviderPipeline(
            RoleModelSlots(slots={"cad_loop_author": candidates}), retry_policy=config.retry
        )
        try:
            result = await asyncio.wait_for(
                pipeline.complete("cad_loop_author", None, _invoke),
                _env_float(MODEL_TIMEOUT_ENV, DEFAULT_MODEL_TIMEOUT_S),
            )
        except TimeoutError as exc:
            raise LoopUnavailableError("no model answered in time") from exc
        except AllProvidersFailedError as exc:
            raise LoopUnavailableError(f"no model could answer: {exc}") from exc
        text = result.get("text", "") if isinstance(result, dict) else str(result or "")
        after = _spent(run_key) if run_key else None
        cost = after - (before or 0.0) if after is not None else None
        return ModelReply(text, answered.get("provider"), answered.get("model"), cost)

    return call


def make_default_critic_call() -> CriticCall:
    """One vision call (``METAFORGE_VISION_MODEL``), priced from the usage store."""

    async def call(image: bytes, mime: str, prompt: str) -> ModelReply:
        from api_gateway.twin.vision import VisionUnavailableError, make_vision_call
        from orchestrator.harness.providers.usage import current_usage_context

        run_key = current_usage_context().run_id or ""
        before = _spent(run_key) if run_key else None
        vision = make_vision_call(
            role="cad_loop_critic",
            purpose="reviewing a CAD model's rendered views",
            timeout_s=_env_float(MODEL_TIMEOUT_ENV, DEFAULT_MODEL_TIMEOUT_S),
        )
        try:
            reply = await vision(image, mime, prompt)
        except VisionUnavailableError as exc:
            raise LoopUnavailableError(str(exc)) from exc
        after = _spent(run_key) if run_key else None
        cost = after - (before or 0.0) if after is not None else None
        if isinstance(reply, str):
            return ModelReply(reply, None, None, cost)
        return ModelReply(reply.text, reply.provider, reply.model, cost)

    return call


def _default_glb(step: bytes) -> bytes:
    from api_gateway.twin.render_recorder import convert_step_to_glb

    return convert_step_to_glb(step, "cad-loop.step")


def _default_blob_writer(node_id: str, filename: str, content: bytes, content_type: str) -> str:
    from digital_twin.storage.work_product_blobs import store_work_product_blob

    return store_work_product_blob(node_id, filename, content, content_type=content_type)


async def _publish_via_seam(project_id: str, event: dict[str, Any]) -> None:
    from mcp_core.operation_events import publish_operation_event

    await publish_operation_event(project_id, event)


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


class CadDesignLoopService:
    """Runs, records, lists and decides CAD design loop runs."""

    def __init__(
        self,
        twin: Any,
        *,
        exact_cad: Any = None,
        author: AuthorCall | None = None,
        critic: CriticCall | None = None,
        glb_converter: GlbConverter | None = None,
        blob_writer: BlobWriter | None = None,
        publisher: Publisher | None = None,
        job_runner: Any = None,
    ) -> None:
        self._twin = twin
        self._exact_cad = exact_cad
        self._author = author
        self._critic = critic
        self._to_glb = glb_converter or _default_glb
        self._write_blob = blob_writer or _default_blob_writer
        self._publish = publisher or _publish_via_seam
        self._runner = job_runner

    # -- seams -----------------------------------------------------------

    def _exact(self) -> Any:
        if self._exact_cad is not None:
            return self._exact_cad
        from api_gateway.twin.exact_cad import get_exact_cad_service

        service = get_exact_cad_service()
        if service is None:
            raise LoopUnavailableError("the CAD adapter is not available")
        return service

    def _job_runner(self) -> Any:
        if self._runner is not None:
            return self._runner
        from tool_registry.jobs import default_job_runner

        return default_job_runner()

    async def _tool(self, tool_id: str, params: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = await self._exact().invoke_tool(tool_id, params)
        return result

    # -- context ---------------------------------------------------------

    async def _psk(self, pid: UUID, request: CadLoopRequest) -> Any:
        from twin_core.items import find_item, parse_item_ref
        from twin_core.models.enums import WorkProductType
        from twin_core.representation import COMPONENT_ID_KEY

        if request.sketch_key:
            try:
                key, _pin = parse_item_ref(request.sketch_key)
            except ValueError as exc:
                raise CadLoopError(str(exc), 400) from exc
            item = await find_item(self._twin, key, pid)
            if item is None or item.head_node_id is None or item.item_type != "parametric_sketch":
                raise CadLoopError(f"no parametric plan {key} in this project", 404)
            return await self._twin.get_work_product(item.head_node_id)
        if not request.component_id:
            raise CadLoopError(
                "name the component's parametric plan (sketch_key) or component", 400
            )
        nodes = await self._twin.list_work_products(
            work_product_type=WorkProductType.PARAMETRIC_SKETCH, project_id=pid
        )
        mine = [
            n for n in nodes if (n.metadata or {}).get(COMPONENT_ID_KEY) == request.component_id
        ]
        if not mine:
            raise CadLoopError(
                f"{request.component_id} has no parametric plan yet; make it editable first", 409
            )
        mine.sort(key=lambda n: n.created_at.isoformat() if n.created_at else "", reverse=True)
        return mine[0]

    async def prepare(self, project_id: str, request: CadLoopRequest) -> LoopContext:
        """Everything the run reads, or :class:`CadLoopError` for a refused request."""
        from api_gateway.twin.exact_cad import cad_key_for_sketch, load_parametric_sketch
        from twin_core.items import find_item
        from twin_core.representation import COMPONENT_ID_KEY

        pid = _pid(project_id)
        with tracer.start_as_current_span("twin.cad_loop.prepare") as span:
            psk = await self._psk(pid, request)
            if psk is None:
                raise CadLoopError("the parametric plan could not be read", 404)
            meta = dict(psk.metadata or {})
            sketch_key = meta.get("item_key")
            name = str(psk.name or "").removeprefix("Parametric sketch: ") or "component"
            cid = str(meta.get(COMPONENT_ID_KEY) or request.component_id or f"PSK:{sketch_key}")
            cad_key = cad_key_for_sketch(str(sketch_key or name))
            cad = await find_item(self._twin, cad_key, pid)
            base = str(cad.head_revision) if cad is not None and cad.head_node_id else None
            span.set_attribute("cad_loop.component", cid)
            span.set_attribute("cad_loop.base_revision", base or "")
            ctx = LoopContext(
                project_id=str(pid),
                component_id=cid,
                component_name=name,
                sketch_key=str(sketch_key) if sketch_key else None,
                sketch_ref=(
                    f"{sketch_key}@{meta.get('item_revision')}"
                    if sketch_key and meta.get("item_revision")
                    else f"node:{psk.id}"
                ),
                cad_key=cad_key,
                base_revision=base,
                intent=(request.intent or "").strip(),
                targets=dict(request.target_dimensions or {}),
                tolerance_mm=request.tolerance_mm or DEFAULT_TOLERANCE_MM,
                views=list(request.views or DEFAULT_VIEWS),
            )
            await self._intent_context(ctx, pid, request)
            await self._manufacturing_context(ctx, pid)
            await self._plan_context(ctx, psk, load_parametric_sketch(meta), request)
            return ctx

    async def _plan_context(
        self, ctx: LoopContext, psk: Any, raw: dict[str, Any], request: CadLoopRequest
    ) -> None:
        answers = {k.strip(): v for k, v in request.answers.items()}
        plan = list(raw.get("feature_plan") or [])
        ctx.feature_plan = [p for p in plan if isinstance(p, dict)]
        # What a person answered, by parameter name, to seed the plan's script.
        inputs: dict[str, Any] = {}
        for i, feature in enumerate(ctx.feature_plan):
            for pname, param in (feature.get("parameters") or {}).items():
                if not isinstance(param, dict):
                    continue
                label = f"{feature.get('id')}.{pname}"
                answer = answers.get(label, answers.get(pname))
                if answer not in (None, "") and i == 0:
                    inputs[pname] = answer
                if pname in _NON_DIMENSIONS:
                    continue
                if answer not in (None, ""):
                    ctx.stated.append(
                        Stated(label, answer, param.get("unit") or "mm", "your answer")
                    )
                elif param.get("status") == "provided" and param.get("value") not in (None, ""):
                    ctx.stated.append(
                        Stated(label, param["value"], param.get("unit") or "mm", "parametric plan")
                    )
                else:
                    ctx.unknowns.append(
                        Unknown(
                            label,
                            param.get("question") or f"What is the {pname} of {feature.get('id')}?",
                            "parametric plan",
                            required=i == 0,
                        )
                    )
        # Seed the first script from the plan when its inputs are complete.
        material = getattr(ctx.process, "material", None)
        if material and getattr(ctx.process, "material_authority", "") != "candidate":
            # The plan's material question is answered by the manufacturing context.
            inputs.setdefault("material", material)
        try:
            source = await self._exact().parametric_source(psk, inputs=inputs or None)
            from domain_agents.shared.cadquery_lowering import compile_design_ir_cadquery
            from twin_core.design_ir.models import DesignIR

            ctx.seed_script, _t, _v = compile_design_ir_cadquery(
                DesignIR.model_validate(source["design_ir"])
            )
            ctx.seed_note = f"compiled from {ctx.sketch_ref} ({source.get('feature_id')})"
        except Exception as exc:  # noqa: BLE001 - the plan is still in the prompt
            ctx.seed_note = f"not seeded: {str(exc)[:200]}"
        # The proposed feature history, when reconstruction made one.
        from api_gateway.twin.reconstruction import proposal_key
        from twin_core.items import find_item

        fhp = await find_item(self._twin, proposal_key(ctx.component_id), _pid(ctx.project_id))
        if fhp is not None and fhp.head_node_id is not None:
            node = await self._twin.get_work_product(fhp.head_node_id)
            content = dict(((node.metadata or {}) if node else {}).get("feature_history") or {})
            ctx.feature_history = [f for f in content.get("features") or [] if isinstance(f, dict)]
            ctx.feature_history_ref = f"{fhp.key}@{fhp.head_revision}"

    async def _intent_context(self, ctx: LoopContext, pid: UUID, request: CadLoopRequest) -> None:
        from api_gateway.twin.reconstruction import _reference_facts

        try:
            from api_gateway.create.routes import get_create_store

            brief = get_create_store().get(str(pid)).brief
            ctx.brief = {k: v.value for k, v in brief.items.items() if v.value}
            for m in brief.measurements:
                ctx.stated.append(
                    Stated(m.label, m.value, m.unit, f"brief ({m.source})", basis=m.status)
                )
        except Exception as exc:  # noqa: BLE001 - no Create record reads as no brief
            logger.debug("cad_loop_brief_unavailable", error=str(exc))
        facts, _about = _reference_facts(pid, ctx.component_name, ctx.component_id)
        for f in facts:
            ctx.references.append(
                {
                    "reference_id": f.reference_id,
                    "role": f.role,
                    "parameter": f.parameter,
                    "value": f.value,
                    "unit": f.unit,
                    "basis": f.basis,
                    "text": f.text,
                }
            )
        try:
            from api_gateway.twin.interface_contracts import get_contract

            stored = await get_contract(self._twin, pid, IFC_CAD_EDA)
        except Exception as exc:  # noqa: BLE001 - no contract reads as none
            logger.debug("cad_loop_contract_unavailable", error=str(exc))
            stored = None
        if stored is not None:
            params = [p.model_dump(mode="json") for p in stored.contract.parameters]
            ctx.contract = {"ref": stored.ref, "title": stored.contract.title, "parameters": params}
            for p in params:
                if p.get("owner") not in ("mechanical", "cad"):
                    continue
                label = f"{IFC_CAD_EDA}.{p.get('name')}"
                if p.get("basis") == "unknown" or p.get("value") in (None, ""):
                    answer = request.answers.get(label, request.answers.get(str(p.get("name"))))
                    if answer not in (None, ""):
                        ctx.stated.append(Stated(label, answer, p.get("unit") or "", "your answer"))
                    elif p.get("unit") in ("mm", "deg"):
                        ctx.unknowns.append(
                            Unknown(
                                label,
                                f"What is {p.get('label') or p.get('name')}?",
                                stored.ref,
                                required=False,
                            )
                        )
                else:
                    ctx.stated.append(
                        Stated(
                            label,
                            p.get("value"),
                            p.get("unit") or "",
                            stored.ref,
                            tolerance=p.get("tolerance"),
                            basis=str(p.get("basis")),
                        )
                    )

    async def _manufacturing_context(self, ctx: LoopContext, pid: UUID) -> None:
        from api_gateway.twin.dfm_exact import _context
        from api_gateway.twin.dfm_findings import dfm_view
        from api_gateway.twin.manufacturing_context import manufacturing_context_block
        from twin_core.dfm import profile_for_process

        block = await manufacturing_context_block(
            pid, ctx.component_name, twin=self._twin, generator=LOOP_TOOL, hint=ctx.intent
        )
        ctx.manufacturing_text = block.text
        ctx.manufacturing_ref = block.ref
        ctx.manufacturing_unresolved = list(block.unresolved)
        stored, process = await _context(self._twin, pid, ctx.component_name, ctx.component_id)
        ctx.stored_context, ctx.process = stored, process
        ctx.profile = profile_for_process(process.process)
        if stored is not None:
            entry = stored.record.get("dimensional_envelope")
            if entry.known and not entry.stale and not str(stored.ref).startswith("MFC-PROJECT"):
                ctx.envelope = _envelope_of(entry.value)
        try:
            view = await dfm_view(self._twin, pid)
            mine = next((c for c in view.components if c.component_id == ctx.component_id), None)
            ctx.dfm_current = mine.model_dump(mode="json") if mine is not None else None
        except Exception as exc:  # noqa: BLE001 - no findings read as none
            logger.debug("cad_loop_dfm_unavailable", error=str(exc))

    # -- records ---------------------------------------------------------

    async def _create_record(
        self, ctx: LoopContext, request: CadLoopRequest, budget: Budget, job_id: str | None
    ) -> Any:
        from twin_core.models.enums import WorkProductType
        from twin_core.models.work_product import WorkProduct

        body = {
            "status": "running",
            "started_at": _now(),
            "job_id": job_id,
            "request": request.model_dump(mode="json"),
            "context": ctx.summary(),
            "budget": budget.as_dict(),
            "iterations": [],
            "stop_reason": None,
            "stop_detail": None,
            "result": None,
            "decision": None,
        }
        now = datetime.now(UTC)
        wp_id = uuid4()
        created = await self._twin.create_work_product(
            WorkProduct(
                id=wp_id,
                name=f"CAD design loop: {ctx.component_name}",
                type=WorkProductType.CAD_DESIGN_LOOP,
                domain="mechanical",
                file_path="",
                content_hash=hashlib.sha256(
                    json.dumps(body["context"], sort_keys=True, default=str).encode()
                ).hexdigest(),
                format="json",
                metadata={
                    META_KEY: body,
                    "component_ref": ctx.component_id,
                    "component_name": ctx.component_name,
                    "base_item_ref": (
                        f"{ctx.cad_key}@{ctx.base_revision}" if ctx.base_revision else None
                    ),
                    "manufacturing_context_ref": ctx.manufacturing_ref,
                    # A proposal, never evidence; the critic validates nothing.
                    "authority": "proposal",
                    "image_files": {},
                },
                created_at=now,
                updated_at=now,
                created_by=LOOP_TOOL,
                project_id=_pid(ctx.project_id),
            )
        )
        return created

    async def _save(self, node: Any, body: dict[str, Any], images: dict[str, str]) -> Any:
        from twin_core.geometry_state import set_node_metadata

        fresh = await self._twin.get_work_product(node.id) or node
        files = dict((fresh.metadata or {}).get("image_files") or {})
        files.update(images)
        await set_node_metadata(self._twin, fresh, **{META_KEY: body, "image_files": files})
        return await self._twin.get_work_product(node.id) or fresh

    def _store_image(self, node_id: str, filename: str, png: bytes) -> str | None:
        try:
            return self._write_blob(node_id, filename, png, "image/png")
        except Exception as exc:  # noqa: BLE001 - the record says the image was not kept
            logger.warning("cad_loop_image_not_stored", filename=filename, error=str(exc)[:200])
            return None

    async def _emit(
        self,
        ctx: LoopContext,
        *,
        op_id: str,
        name: str,
        event_type: str,
        sequence: int,
        message: str,
        progress: tuple[int, int] | None = None,
        artifact_refs: list[dict[str, Any]] | None = None,
        severity: str = "info",
        state: str = "running",
    ) -> None:
        event: dict[str, Any] = {
            "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"metaforge:{op_id}:{name}")),
            "operation_id": op_id,
            "kind": "job",
            "sequence": sequence,
            "type": event_type,
            "state": state,
            "domain": "mechanical",
            "stage_id": name,
            "stage_title": message[:200],
            "occurred_at": _now(),
            "severity": severity,
            "message": message[:500],
            "artifact_refs": artifact_refs or [],
            "target_key": f"{TARGET_PREFIX}{ctx.cad_key}"[:200],
        }
        if ctx.base_revision:
            event["base_revision"] = ctx.base_revision
        if progress is not None:
            event["progress"] = {"done": progress[0], "total": progress[1], "unit": "iterations"}
        try:
            await self._publish(ctx.project_id, event)
        except Exception as exc:  # noqa: BLE001 - a status line never fails the run
            logger.info("cad_loop_event_dropped", name=name, error=str(exc))

    # -- one iteration ---------------------------------------------------

    async def _build(self, script: str, ctx: LoopContext, record: dict[str, Any]) -> bytes | None:
        """Build and check; fills ``build``, ``validity``, ``measurements``, ``dfm``."""
        try:
            built = await self._tool("cadquery.execute_script", {"script": script})
        except Exception as exc:  # noqa: BLE001 - the error goes back to the author
            record["build"] = {"ok": False, "error": str(exc)[:600]}
            return None
        step_b64 = built.get("step_base64")
        if not step_b64:
            record["build"] = {"ok": False, "error": "the script built no STEP (is result set?)"}
            return None
        step = base64.b64decode(step_b64)
        bbox = built.get("bounding_box") if isinstance(built.get("bounding_box"), dict) else None
        record["build"] = {"ok": True, "step_sha256": hashlib.sha256(step).hexdigest()}
        record["measurements"] = {
            "bounding_box": bbox,
            "size_mm": _bbox_size(bbox),
            "volume_mm3": built.get("volume_mm3"),
            "surface_area_mm2": built.get("surface_area_mm2"),
        }
        try:
            validity = await self._tool(
                "cadquery.validate_shape",
                {"step_base64": step_b64, "expect_solid": True},
            )
            outcome = str(validity.get("outcome") or "not_evaluated")
        except Exception as exc:  # noqa: BLE001 - no evidence is not a pass
            validity, outcome = (
                {"findings": [{"check": "kernel", "detail": str(exc)[:300]}]},
                ("not_evaluated"),
            )
        record["validity"] = {
            "outcome": outcome,
            "findings": list(validity.get("findings") or [])[:20],
            "solid_count": validity.get("solid_count"),
            "tool": "cadquery.validate_shape",
        }
        record["dimension_checks"] = dimension_checks(ctx, bbox)
        record["dfm"] = await self._dfm(ctx, step_b64, bbox, record)
        return step

    async def _dfm(
        self, ctx: LoopContext, step_b64: str, bbox: dict[str, Any] | None, record: dict[str, Any]
    ) -> dict[str, Any]:
        """The profile's exact checks (FDM) on the candidate, else its heuristic rules."""
        from twin_core.dfm import ComponentGeometry, Measure, evaluate

        profile = ctx.profile
        ref = f"cad-loop:{ctx.cad_key}#{record['n']}"
        if profile is None:
            return {
                "status": "not_assessed",
                "kind": "none",
                "note": "No process is selected, so no manufacturing rules apply yet.",
                "findings": [],
                "not_assessed": [],
            }
        if profile.exact_checks:
            from api_gateway.twin.dfm_exact import _context_facts, _signatures, resolve_orientation
            from twin_core.dfm.exact import evaluate_exact

            orientation = resolve_orientation(ctx.stored_context, None)
            measured: dict[str, Any] | None = None
            error: str | None = None
            try:
                measured = await self._tool(
                    "cadquery.dfm_measure",
                    {"step_base64": step_b64, "build_direction": list(orientation.direction)},
                )
            except Exception as exc:  # noqa: BLE001 - an incomplete analysis, said so
                error = str(exc)[:300]
            if measured is not None:
                wall = measured.get("wall_thickness") or {}
                record.setdefault("measurements", {})["dfm_measure"] = {
                    "min_wall_mm": wall.get("min_mm"),
                    "min_hole_diameter_mm": measured.get("min_hole_diameter_mm"),
                    "min_boss_diameter_mm": measured.get("min_boss_diameter_mm"),
                    "build_direction": measured.get("build_direction"),
                }
            tolerances, goal = _context_facts(ctx.stored_context)
            fs = evaluate_exact(
                component_id=ctx.component_id,
                component_name=ctx.component_name,
                geometry_ref=ref,
                context=ctx.process,
                profile=profile,
                measured=measured,
                measure_error=error,
                signatures=_signatures(measured),
                tolerances_stated=tolerances,
                production_goal=goal,
                build_direction_source=orientation.source,
                build_rotation_deg=orientation.rotation_deg,
            )
        else:
            size = _bbox_size(bbox)
            measures = (
                [
                    Measure(
                        quantity="envelope",
                        value=[size["x"], size["y"], size["z"]],
                        confidence="exact",
                        source="exact B-Rep (candidate)",
                        evidence_ref=ref,
                    )
                ]
                if size
                else []
            )
            fs = evaluate(
                ComponentGeometry(
                    component_id=ctx.component_id,
                    name=ctx.component_name,
                    level="exact",
                    geometry_refs=[ref],
                    measures=measures,
                ),
                ctx.process,
                profile=profile,
            )
        out = fs.model_dump(mode="json")
        return {
            "status": out.get("status"),
            "kind": out.get("kind"),
            "profile": profile.ref,
            "note": out.get("note"),
            "findings": out.get("findings") or [],
            "not_assessed": out.get("not_assessed") or [],
            "complete": out.get("complete"),
        }

    async def _render(
        self, ctx: LoopContext, step: bytes, record: dict[str, Any], node: Any
    ) -> tuple[bytes | None, dict[str, str]]:
        """GLB through OCCT, then render.model_views. ``(sheet png, stored images)``."""
        try:
            glb = await asyncio.to_thread(self._to_glb, step)
        except Exception as exc:  # noqa: BLE001 - recorded; the run stops honestly
            _metric("record_cad_loop_render", "convert_failed")
            logger.warning("cad_loop_render_failed", stage="convert", error=str(exc)[:200])
            record["render"] = {"ok": False, "error": f"STEP to GLB failed: {str(exc)[:300]}"}
            return None, {}
        size = (record.get("measurements") or {}).get("size_mm") or {}
        try:
            rendered = await self._tool(
                "render.model_views",
                {
                    "glb_base64": base64.b64encode(glb).decode("ascii"),
                    "views": list(ctx.views),
                    "up_axis": "z",
                    "dimensions": {**size, "source": "cadquery bounding box"} if size else None,
                    "title": f"{ctx.component_name} · iteration {record['n']}",
                    "sheet": True,
                },
            )
        except Exception as exc:  # noqa: BLE001 - recorded; the run stops honestly
            _metric("record_cad_loop_render", "render_failed")
            logger.warning("cad_loop_render_failed", stage="render", error=str(exc)[:200])
            record["render"] = {"ok": False, "error": f"the render failed: {str(exc)[:300]}"}
            return None, {}
        _metric("record_cad_loop_render", "rendered")
        images: dict[str, str] = {}
        views: list[dict[str, Any]] = []
        node_id = str(node.id)
        for v in rendered.get("views") or []:
            name = re.sub(r"[^a-z0-9_]+", "-", str(v.get("view") or "view").lower())
            filename = f"iter-{record['n']}-{name}.png"
            key = self._store_image(node_id, filename, base64.b64decode(v.get("png_base64") or ""))
            if key:
                images[filename] = key
            views.append(
                {
                    "view": v.get("view"),
                    "label": v.get("label"),
                    "projection": v.get("projection"),
                    "mm_per_px": v.get("mm_per_px"),
                    "file": filename if key else None,
                }
            )
        sheet_b64 = rendered.get("sheet_png_base64")
        sheet = base64.b64decode(sheet_b64) if sheet_b64 else None
        sheet_file = None
        if sheet:
            sheet_file = f"iter-{record['n']}-sheet.png"
            key = self._store_image(node_id, sheet_file, sheet)
            if key:
                images[sheet_file] = key
            else:
                sheet_file = None
        record["render"] = {
            "ok": True,
            "views": views,
            "sheet": sheet_file,
            "renderer": rendered.get("renderer"),
            "mesh_bbox_mm": rendered.get("bbox_mm"),
        }
        return sheet, images

    async def _critique(
        self, ctx: LoopContext, sheet: bytes, record: dict[str, Any], budget: Budget
    ) -> Critique | None:
        critic = self._critic or make_default_critic_call()
        reply = await critic(sheet, "image/png", critic_prompt(ctx, record))
        budget.add(reply.cost_usd)
        _metric("record_cad_loop_model_call", "critic", reply.cost_usd)
        record["models"]["critic"] = {
            "provider": reply.provider,
            "model": reply.model,
            "cost_usd": reply.cost_usd,
        }
        try:
            critique = parse_critique(reply.text)
        except ValueError as exc:
            _metric("record_cad_loop_critic", "unparsed")
            record["critique"] = {
                "satisfied": False,
                "verdict": "unparsed",
                "reasons": [str(exc)],
                "issues": [],
            }
            return None
        # A "satisfied" critic that still lists a blocking or major issue is not satisfied.
        serious = [i for i in critique.issues if i.severity in ("blocking", "major")]
        satisfied = critique.satisfied and not serious
        _metric("record_cad_loop_critic", "satisfied" if satisfied else "not_satisfied")
        record["critique"] = {
            "satisfied": satisfied,
            "verdict": "satisfied" if satisfied else "not_satisfied",
            "reasons": critique.reasons,
            "issues": [i.model_dump() for i in critique.issues],
            # Advisory only: the critic's verdict never validates geometry (3D-RC-012).
            "authority": "advisory",
        }
        return critique

    # -- the run ---------------------------------------------------------

    def _budget(self, request: CadLoopRequest) -> Budget:
        return Budget(
            max_calls=request.max_model_calls or request.max_iterations * 2,
            max_usd=request.max_spend_usd or _env_float(SPEND_ENV, DEFAULT_MAX_SPEND_USD),
        )

    async def run(
        self,
        project_id: str,
        request: CadLoopRequest,
        *,
        request_context: dict[str, Any] | None = None,
        prepared: LoopContext | None = None,
    ) -> dict[str, Any]:
        """One whole run, recorded. See the module docstring."""
        from orchestrator.harness.providers.usage import usage_scope
        from tool_registry.jobs import current_job_id

        started = time.monotonic()
        ctx = prepared or await self.prepare(project_id, request)
        job_id = current_job_id()
        budget = self._budget(request)
        node = await self._create_record(ctx, request, budget, job_id)
        op_id = f"job:{job_id}" if job_id else f"job:cad-loop-{node.id}"
        run_key = f"cad-loop-{node.id}"
        body: dict[str, Any] = dict((node.metadata or {})[META_KEY])
        body["request_context"] = dict(request_context or {})
        iterations: list[dict[str, Any]] = []
        stop: StopReason | None = None
        detail = ""
        questions: list[dict[str, str]] = []
        with (
            tracer.start_as_current_span("twin.cad_loop.run") as span,
            usage_scope(run_id=run_key, role="cad_design_loop", caller=LOOP_TOOL),
        ):
            span.set_attribute("cad_loop.node_id", str(node.id))
            span.set_attribute("cad_loop.component", ctx.component_id)
            author = self._author or make_default_author_call()
            previous: dict[str, Any] | None = None
            for n in range(1, request.max_iterations + 1):
                refusal = budget.refusal()
                if refusal:
                    stop, detail = StopReason.SPEND_CAP, refusal
                    break
                record: dict[str, Any] = {
                    "n": n,
                    "started_at": _now(),
                    "models": {},
                    "manufacturing_context_ref": ctx.manufacturing_ref,
                }
                try:
                    reply_raw = await author(
                        author_prompt(
                            ctx,
                            iteration=n,
                            max_iterations=request.max_iterations,
                            previous=previous,
                            allow_working_values=request.allow_working_values,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - the run stops honestly
                    span.record_exception(exc)
                    stop, detail = StopReason.FAILED, f"the model could not be asked: {exc}"
                    break
                budget.add(reply_raw.cost_usd)
                _metric("record_cad_loop_model_call", "author", reply_raw.cost_usd)
                record["models"]["author"] = {
                    "provider": reply_raw.provider,
                    "model": reply_raw.model,
                    "cost_usd": reply_raw.cost_usd,
                }
                try:
                    reply = parse_author(reply_raw.text)
                except ValueError as exc:
                    record["build"] = {"ok": False, "error": str(exc)}
                    record["script"] = ""
                    iterations.append(record)
                    _metric("record_cad_loop_iteration", "build_failed")
                    previous = record
                    await self._checkpoint(node, body, iterations, budget, {})
                    continue
                record.update(
                    script=reply.script,
                    script_sha256=hashlib.sha256(reply.script.encode()).hexdigest(),
                    summary=reply.summary,
                    focus=reply.focus,
                    assumptions=[a.model_dump(mode="json") for a in reply.assumptions],
                )
                if previous is not None and previous.get("script"):
                    record["script_diff_from"] = previous["n"]
                focus = reply.focus or ("first version" if n == 1 else "revising")
                await self._emit(
                    ctx,
                    op_id=op_id,
                    name=f"iteration_{n}",
                    event_type="milestone",
                    sequence=100 + n * 10,
                    message=f"Iteration {n}: {focus}",
                    progress=(n - 1, request.max_iterations),
                )
                questions = unknowns_unanswered(
                    ctx, reply, allow_working_values=request.allow_working_values
                )
                if reply.status == "question" or questions or not reply.script.strip():
                    record["questions"] = questions or [
                        {
                            "name": "script",
                            "question": "The model wrote no script and asked nothing; "
                            "add detail to the request.",
                            "source": "model",
                        }
                    ]
                    questions = record["questions"]
                    iterations.append(record)
                    _metric("record_cad_loop_iteration", "question")
                    stop = StopReason.NEEDS_INPUT
                    detail = "; ".join(q["question"] for q in questions)[:400]
                    break
                step = await self._build(reply.script, ctx, record)
                if step is None:
                    iterations.append(record)
                    _metric("record_cad_loop_iteration", "build_failed")
                    previous = record
                    await self._checkpoint(node, body, iterations, budget, {})
                    continue
                sheet, images = await self._render(ctx, step, record, node)
                if sheet is None:
                    iterations.append(record)
                    _metric("record_cad_loop_iteration", "unrendered")
                    stop = StopReason.FAILED
                    detail = (record.get("render") or {}).get("error") or "no render"
                    await self._checkpoint(node, body, iterations, budget, images)
                    break
                refusal = budget.refusal()
                if refusal:
                    iterations.append(record)
                    _metric("record_cad_loop_iteration", "built")
                    stop, detail = StopReason.SPEND_CAP, refusal + " before the review"
                    await self._checkpoint(node, body, iterations, budget, images)
                    break
                try:
                    critique = await self._critique(ctx, sheet, record, budget)
                except Exception as exc:  # noqa: BLE001 - the run stops honestly
                    span.record_exception(exc)
                    _metric("record_cad_loop_critic", "unavailable")
                    iterations.append(record)
                    stop, detail = StopReason.FAILED, f"the critic could not be asked: {exc}"
                    await self._checkpoint(node, body, iterations, budget, images)
                    break
                outcome = (
                    "built"
                    if (record.get("validity") or {}).get("outcome") == "pass"
                    else "invalid"
                )
                _metric("record_cad_loop_iteration", outcome)
                record["gate"] = self._gate(record, critique)
                record["ended_at"] = _now()
                iterations.append(record)
                sheet_file = (record.get("render") or {}).get("sheet")
                await self._emit(
                    ctx,
                    op_id=op_id,
                    name=f"iteration_{n}_views",
                    event_type="partial_result",
                    sequence=105 + n * 10,
                    message=f"Iteration {n}: {self._gate_words(record['gate'])}",
                    progress=(n, request.max_iterations),
                    artifact_refs=(
                        [
                            {
                                "kind": "image",
                                "id": f"{node.id}:{sheet_file}",
                                "label": f"Iteration {n} views",
                                "url": f"/api/v1/twin/nodes/{node.id}/files/{sheet_file}",
                                "authority": "preview",
                            }
                        ]
                        if sheet_file
                        else []
                    ),
                )
                await self._checkpoint(node, body, iterations, budget, images)
                previous = record
                if record["gate"]["passed"]:
                    stop = StopReason.SATISFIED
                    detail = STOP_WORDS[StopReason.SATISFIED]
                    break
            if stop is None:
                stop = StopReason.ITERATION_CAP
                detail = f"{request.max_iterations} iterations ran"
            span.set_attribute("cad_loop.stop_reason", stop.value)
            span.set_attribute("cad_loop.iterations", len(iterations))
        result = self._result(ctx, iterations, stop)
        body.update(
            status="finished",
            ended_at=_now(),
            duration_s=round(time.monotonic() - started, 3),
            stop_reason=stop.value,
            stop_words=STOP_WORDS[stop],
            stop_detail=detail,
            questions=questions if stop is StopReason.NEEDS_INPUT else [],
            result=result,
            budget=budget.as_dict(),
            iterations=iterations,
        )
        node = await self._save(node, body, {})
        _metric("record_cad_loop_run", stop.value)
        failed = stop is StopReason.FAILED
        await self._emit(
            ctx,
            op_id=op_id,
            name="loop_finished",
            # The job itself ends through the runner; this line only says why the
            # loop stopped, and asks for a person when there is something to decide.
            event_type="review_required" if result else "milestone",
            sequence=900,
            message=(
                f"{STOP_WORDS[stop]}. "
                + ("A proposed version is ready to review." if result else detail)
            ),
            severity="warning" if stop is not StopReason.SATISFIED else "info",
            state="waiting_for_user" if result else "running",
            artifact_refs=[
                {
                    "kind": "work_product",
                    "id": str(node.id),
                    "label": f"CAD design loop: {ctx.component_name}",
                    "authority": "preview",
                }
            ],
        )
        if failed:
            logger.warning("cad_loop_failed", node_id=str(node.id), detail=detail[:300])
        logger.info(
            "cad_loop_finished",
            node_id=str(node.id),
            component=ctx.component_id,
            stop_reason=stop.value,
            iterations=len(iterations),
            model_calls=budget.calls,
            spend_usd=round(budget.usd, 4),
        )
        return self.view_of(node)

    async def _checkpoint(
        self,
        node: Any,
        body: dict[str, Any],
        iterations: list[dict[str, Any]],
        budget: Budget,
        images: dict[str, str],
    ) -> None:
        body["iterations"] = list(iterations)
        body["budget"] = budget.as_dict()
        try:
            await self._save(node, body, images)
        except Exception as exc:  # noqa: BLE001 - the final save tries again
            logger.warning("cad_loop_checkpoint_failed", node_id=str(node.id), error=str(exc))

    @staticmethod
    def _gate(record: dict[str, Any], critique: Critique | None) -> dict[str, Any]:
        """The stop rule's four parts, each with its own answer."""
        validity = (record.get("validity") or {}).get("outcome")
        blocking = [
            f
            for f in (record.get("dfm") or {}).get("findings") or []
            if f.get("severity") == "blocking"
        ]
        dims = record.get("dimension_checks") or []
        critic_ok = bool((record.get("critique") or {}).get("satisfied")) and critique is not None
        parts = {
            "critic_satisfied": critic_ok,
            "validity_pass": validity == "pass",
            "no_blocking_dfm": not blocking,
            "dimensions_in_tolerance": all(d.get("ok") for d in dims),
        }
        return {**parts, "passed": all(parts.values()), "dimensions_checked": len(dims)}

    @staticmethod
    def _gate_words(gate: dict[str, Any]) -> str:
        if gate.get("passed"):
            return "the critic is satisfied and every check passed"
        open_ = []
        if not gate.get("validity_pass"):
            open_.append("the geometry check did not pass")
        if not gate.get("no_blocking_dfm"):
            open_.append("a blocking manufacturing finding remains")
        if not gate.get("dimensions_in_tolerance"):
            open_.append("a dimension is out of tolerance")
        if not gate.get("critic_satisfied"):
            open_.append("the critic raised issues")
        return "; ".join(open_)

    def _result(
        self, ctx: LoopContext, iterations: list[dict[str, Any]], stop: StopReason
    ) -> dict[str, Any] | None:
        """The proposed version: the last iteration that built and passed validity."""
        candidates = [
            r
            for r in iterations
            if (r.get("build") or {}).get("ok")
            and (r.get("validity") or {}).get("outcome") == "pass"
        ]
        if not candidates:
            return None
        best = next((r for r in reversed(candidates) if (r.get("gate") or {}).get("passed")), None)
        best = best or candidates[-1]
        return {
            "status": "proposed",
            "iteration": best["n"],
            "script": best.get("script"),
            "script_sha256": best.get("script_sha256"),
            "step_sha256": (best.get("build") or {}).get("step_sha256"),
            "cad_key": ctx.cad_key,
            "base_revision": ctx.base_revision,
            "satisfied": stop is StopReason.SATISFIED and (best.get("gate") or {}).get("passed"),
            "open_issues": self._open_issues(best),
            "assumptions": best.get("assumptions") or [],
            # The critic never validates: only the exact path does, on acceptance.
            "validated": False,
            "authority": "proposal",
        }

    @staticmethod
    def _open_issues(record: dict[str, Any]) -> list[str]:
        """What is still open on a proposed version, in plain words."""
        out: list[str] = []
        for f in (record.get("dfm") or {}).get("findings") or []:
            out.append(f"Manufacturing: {f.get('message') or f.get('rule_title')}")
        for d in record.get("dimension_checks") or []:
            if not d.get("ok"):
                out.append(
                    f"Size along {d['axis'].upper()} is {d['measured']:g} mm, wanted {d['wanted']}"
                )
        for i in (record.get("critique") or {}).get("issues") or []:
            out.append(f"Review ({i.get('severity')}): {i.get('description')}")
        for a in record.get("assumptions") or []:
            out.append(
                f"Working value, please confirm: {a.get('name')} = {a.get('value')} "
                f"{a.get('unit') or ''}".strip()
            )
        return out[:30]

    # -- start, read, decide ---------------------------------------------

    async def start(
        self,
        project_id: str,
        request: CadLoopRequest,
        *,
        session_id: str,
        wait: bool = False,
        request_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start a run as a job (or run it now with ``wait``). Refuses a bad request at once."""
        ctx = await self.prepare(project_id, request)
        if wait:
            return await self.run(
                project_id, request, request_context=request_context, prepared=ctx
            )
        runner = self._job_runner()
        arguments = {"project_id": str(project_id), **request.model_dump(mode="json")}
        job, reused = await runner.start(
            LOOP_TOOL,
            arguments,
            lambda: self.run(project_id, request, request_context=request_context),
            session_id=session_id,
            project_id=str(project_id),
            expected_seconds=EXPECTED_SECONDS,
            target_key=f"{TARGET_PREFIX}{ctx.cad_key}",
            base_revision=ctx.base_revision,
        )
        logger.info(
            "cad_loop_job_started",
            job_id=job.job_id,
            reused=reused,
            component=ctx.component_id,
            base_revision=ctx.base_revision,
        )
        return dict(runner.reply(job, reused))

    async def start_from_tool(
        self,
        project_id: str,
        arguments: dict[str, Any],
        *,
        session_id: str,
        wait: bool,
        request_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """``twin.design_cad_loop``: the tool's arguments, checked; ``ValueError`` if refused."""
        try:
            request = CadLoopRequest.model_validate(arguments)
        except ValidationError as exc:
            raise ValueError(f"{LOOP_TOOL}: {exc.errors()[0].get('msg')}") from exc
        try:
            return await self.start(
                project_id,
                request,
                session_id=session_id,
                wait=wait,
                request_context=request_context,
            )
        except CadLoopError as exc:
            raise ValueError(f"{LOOP_TOOL}: {exc}") from exc

    def retry(self, arguments: dict[str, Any]) -> Callable[[], Awaitable[dict[str, Any]]]:
        """The work of a retried run, from its stored arguments."""
        project_id = str(arguments.get("project_id") or "")
        if not project_id:
            raise ValueError("the failed run names no project")
        request = CadLoopRequest.model_validate(
            {k: v for k, v in arguments.items() if k != "project_id"}
        )
        return lambda: self.run(project_id, request)

    @staticmethod
    def view_of(node: Any) -> dict[str, Any]:
        meta = dict(node.metadata or {})
        body = dict(meta.get(META_KEY) or {})
        return {
            "node_id": str(node.id),
            "name": node.name,
            "component_id": meta.get("component_ref"),
            "component_name": meta.get("component_name"),
            "created_at": node.created_at.isoformat() if node.created_at else None,
            "authority": "proposal",
            **body,
        }

    async def runs(
        self, project_id: str, *, component_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Every run of the project (or one component), newest first."""
        from twin_core.models.enums import WorkProductType

        nodes = await self._twin.list_work_products(
            work_product_type=WorkProductType.CAD_DESIGN_LOOP, project_id=_pid(project_id)
        )
        if component_id:
            nodes = [n for n in nodes if (n.metadata or {}).get("component_ref") == component_id]
        nodes.sort(key=lambda n: n.created_at.isoformat() if n.created_at else "", reverse=True)
        return [self.view_of(n) for n in nodes]

    async def get(self, project_id: str, node_id: str) -> Any:
        try:
            node = await self._twin.get_work_product(UUID(str(node_id)))
        except ValueError as exc:
            raise CadLoopError(f"{node_id!r} is not a node id", 400) from exc
        meta = (getattr(node, "metadata", None) or {}) if node is not None else {}
        if (
            node is None
            or META_KEY not in meta
            or str(getattr(node, "project_id", "") or "") != str(_pid(project_id))
        ):
            raise CadLoopError("no such CAD design loop run in this project", 404)
        return node

    async def decide(
        self, project_id: str, node_id: str, *, decision: str, actor: str, session_id: str
    ) -> dict[str, Any]:
        """A person's decision: ``accept`` builds the proposal through the exact path.

        The head moves only if that build passes its validity check, and only
        from the base revision the run started from; ``reject`` keeps the
        previous version. Nothing is ever accepted on a person's behalf.
        """
        from twin_core.items import ItemRevisionConflictError, find_item

        node = await self.get(project_id, node_id)
        body = dict((node.metadata or {}).get(META_KEY) or {})
        if body.get("status") != "finished":
            raise CadLoopError("this run is still working", 409)
        if body.get("decision"):
            raise CadLoopError(f"this run was already decided ({body['decision']['decision']})")
        result = body.get("result")
        if decision == "accept" and not result:
            raise CadLoopError("this run proposed no version to use", 409)
        entry: dict[str, Any] = {"decision": decision, "by": actor, "at": _now()}
        with tracer.start_as_current_span("twin.cad_loop.decide") as span:
            span.set_attribute("cad_loop.decision", decision)
            if decision == "accept":
                assert result is not None
                exact = self._exact()
                pid = _pid(project_id)
                cad = await find_item(self._twin, result["cad_key"], pid)
                try:
                    if cad is not None and cad.head_node_id is not None:
                        started = await exact.start_build(
                            project_id=str(pid),
                            item_key=result["cad_key"],
                            base_revision=result.get("base_revision") or cad.head_revision,
                            script=result["script"],
                            session_id=session_id,
                        )
                    elif result.get("base_revision"):
                        raise ItemRevisionConflictError(
                            f"{result['cad_key']} is gone since this run started"
                        )
                    else:
                        started = await self._start_first_build(
                            exact, str(pid), result, body, session_id
                        )
                except ItemRevisionConflictError as exc:
                    raise CadLoopError(
                        f"{exc}. The model changed since this run started; run the loop again "
                        "on the current version.",
                        409,
                    ) from exc
                entry["build"] = {
                    "job_id": started.get("job_id"),
                    "status": started.get("status"),
                    "tool": "twin.build_exact_cad",
                }
            body["decision"] = entry
            if result:
                result["status"] = "accepted" if decision == "accept" else "rejected"
                body["result"] = result
            node = await self._save(node, body, {})
        logger.info("cad_loop_decided", node_id=str(node.id), decision=decision, actor=actor)
        return self.view_of(node)

    async def _start_first_build(
        self,
        exact: Any,
        project_id: str,
        result: dict[str, Any],
        body: dict[str, Any],
        session_id: str,
    ) -> dict[str, Any]:
        """The component's first exact revision, built from the accepted script as a job."""
        from api_gateway.twin.exact_cad import EXACT_BUILD_EXPECTED_SECONDS, EXACT_BUILD_TOOL

        context = body.get("context") or {}
        sketch_ref = str(context.get("sketch_ref") or "")
        psk_key = sketch_ref.split("@", 1)[0]
        psk_item = await exact._parametric_item(project_id, psk_key)
        psk = await exact._wp(psk_item.head_node_id)
        source = exact.parse_source(None, result["script"])
        source["parametric_sketch_ref"] = sketch_ref
        name = str(context.get("component_name") or psk.name)
        runner = self._job_runner()
        arguments = {
            "project_id": project_id,
            "item_key": result["cad_key"],
            "script": result["script"],
        }
        job, reused = await runner.start(
            EXACT_BUILD_TOOL,
            arguments,
            lambda: exact._build_new_item(
                project_id=project_id,
                cad_key=result["cad_key"],
                name=name,
                source=source,
                psk=psk,
            ),
            session_id=session_id,
            project_id=project_id,
            expected_seconds=EXACT_BUILD_EXPECTED_SECONDS,
            target_key=result["cad_key"],
        )
        return dict(runner.reply(job, reused))


def make_cad_design_loop_service(
    twin: Any, *, exact_cad: Any = None, job_runner: Any = None
) -> CadDesignLoopService:
    """The service with the gateway's model providers, OCCT converter and blob store."""
    service = CadDesignLoopService(twin, exact_cad=exact_cad, job_runner=job_runner)
    try:
        from tool_registry.jobs import register_retry

        register_retry(LOOP_TOOL, service.retry)
    except Exception as exc:  # noqa: BLE001 - a run without retry still runs
        logger.debug("cad_loop_retry_not_registered", error=str(exc))
    return service


_service: CadDesignLoopService | None = None


def set_cad_design_loop_service(service: CadDesignLoopService | None) -> None:
    global _service  # noqa: PLW0603
    _service = service


def get_cad_design_loop_service() -> CadDesignLoopService | None:
    return _service


__all__ = [
    "LOOP_TOOL",
    "STOP_WORDS",
    "AuthorReply",
    "Budget",
    "CadDesignLoopService",
    "CadLoopError",
    "CadLoopRequest",
    "Critique",
    "LoopContext",
    "LoopUnavailableError",
    "ModelReply",
    "StopReason",
    "author_prompt",
    "critic_prompt",
    "dimension_checks",
    "get_cad_design_loop_service",
    "make_cad_design_loop_service",
    "parse_author",
    "parse_critique",
    "set_cad_design_loop_service",
    "unknowns_unanswered",
]
