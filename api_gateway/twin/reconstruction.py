"""Parametric reconstruction: an accepted concept becomes a proposed feature history.

Spec section 4B and 3D-RC-002. For each component of the accepted concept
assembly this proposes an editable feature history
(:class:`~twin_core.design_ir.feature_history.FeatureHistoryProposal`) and
stores it as a ``feature_history_proposal`` definition item (``FHP-<component>``)
that ``DEPENDS_ON``:

* the accepted concept assembly decision,
* the L1 block layout it read (and the structured mesh drawn from it),
* the references whose facts it used,
* the component's sketches (``SKT``, ``SKI``, ``PSK``),
* the manufacturing context revision.

It is pinned to the item revisions among those (layout, sketches, context), so
a later revision of any of them marks the proposal stale.

Two passes:

* **deterministic** (:func:`twin_core.design_ir.feature_history.deterministic_proposal`):
  L1 boxes and cylinders, parametric sketch profiles, a shell for an
  enclosure, openings from reference facts, bosses for fastened joints. Every
  number is the layout's estimate or a reference fact, labelled.
* **optional model pass** (``use_llm``): names and orders the features and
  suggests missing ones, through the gateway's provider chain. It never
  supplies a number; its suggestions are marked ``suggested_by: llm``.

Accepting (:func:`accept_proposal`) turns accepted features into parametric
sketches (``PSK-REC-<component>``) with a feature plan whose unconfirmed values
stay ``required_input``. Each one is held for the parametric sketch approval
(``/v1/approvals``) before the exact path builds it, and binds the component,
so its maturity becomes Parametric only then (a proposal is never read as
maturity).

Each request is one operation (``job:reconstruction-<project>``, domain
``mechanical``): ``preview_ready`` when proposals are stored,
``review_required`` when accepted sketches await sign-off, ``failed`` on error.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

from api_gateway.twin.item_revisions import finish_definition_revision, plan_definition_revision
from observability.tracing import get_tracer
from twin_core.design_ir.feature_history import (
    PRINCIPAL_PLACEMENT_KEY,
    AcceptanceError,
    ContextFacts,
    FactInput,
    FeatureHistoryProposal,
    ProposalInputs,
    ReconstructionInputs,
    accept_features,
    apply_llm_pass,
    deterministic_proposal,
    inputs_hash,
    llm_prompt,
    mark_llm_unavailable,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.reconstruction")

ITEM_TYPE = "feature_history_proposal"
META_KEY = "feature_history"
EDGE_KIND = "reconstruction"
PSK_PREFIX = "PSK-REC-"
LLM_TIMEOUT_ENV = "METAFORGE_RECONSTRUCTION_LLM_TIMEOUT_S"
DEFAULT_LLM_TIMEOUT_S = 60.0

_metrics: Any = None


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-gateway")
    return _metrics


def _metric(name: str, *args: str) -> None:
    try:
        getattr(_collector(), name)(*args)
    except Exception as exc:  # noqa: BLE001 - a counter never fails a request
        logger.debug("reconstruction_metric_failed", metric=name, error=str(exc))


class ReconstructionError(ValueError):
    """A request reconstruction refuses. ``status`` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# The optional model pass (through the gateway's provider chain)
# ---------------------------------------------------------------------------


class LlmUnavailableError(RuntimeError):
    """No model could be asked, or none answered in time."""


#: ``prompt -> (text, provider, model)``.
LlmCall = Callable[[str], Awaitable[tuple[str, str | None, str | None]]]
_llm_call: LlmCall | None = None


def set_llm_call(call: LlmCall | None) -> None:
    """Install the model call (tests); ``None`` restores the provider chain."""
    global _llm_call  # noqa: PLW0603
    _llm_call = call


def _llm_timeout() -> float:
    try:
        value = float(os.environ.get(LLM_TIMEOUT_ENV, "") or DEFAULT_LLM_TIMEOUT_S)
    except ValueError:
        return DEFAULT_LLM_TIMEOUT_S
    return value if value > 0 else DEFAULT_LLM_TIMEOUT_S


def make_default_llm_call(*, invoke: Any = None, provider_config: Any = None) -> LlmCall:
    """A text call over the chat provider chain (``orchestrator.harness.providers``)."""

    async def call(prompt: str) -> tuple[str, str | None, str | None]:
        from orchestrator.harness.providers import (
            AllProvidersFailedError,
            ProviderPipeline,
            RoleModelSlots,
            default_invoke,
        )
        from orchestrator.harness.providers.usage import usage_scope

        try:
            if provider_config is None:
                from api_gateway.chat.harness_backend import provider_config_from_env

                config = provider_config_from_env()
            else:
                config = provider_config
            candidates = list(config.slots.candidates("generator"))
        except Exception as exc:  # noqa: BLE001 - reported as unavailable
            raise LlmUnavailableError(f"no model provider is configured: {exc}") from exc
        if not candidates:
            raise LlmUnavailableError("no model provider is configured")
        answered: dict[str, str] = {}
        send = invoke or default_invoke

        async def _invoke(spec: Any, _request: Any) -> Any:
            request = {
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1500,
                "temperature": 0.0,
            }
            result = await send(spec, request)
            answered.update(provider=spec.name, model=spec.model)
            return result

        pipeline = ProviderPipeline(
            RoleModelSlots(slots={"reconstruction": candidates}), retry_policy=config.retry
        )
        try:
            with usage_scope(role="reconstruction"):
                result = await asyncio.wait_for(
                    pipeline.complete("reconstruction", None, _invoke), _llm_timeout()
                )
        except TimeoutError as exc:
            raise LlmUnavailableError("no model answered in time") from exc
        except AllProvidersFailedError as exc:
            raise LlmUnavailableError(f"no model could answer: {exc}") from exc
        text = result.get("text", "") if isinstance(result, dict) else str(result or "")
        return text, answered.get("provider"), answered.get("model")

    return call


async def _llm_pass(proposal: FeatureHistoryProposal) -> FeatureHistoryProposal:
    with tracer.start_as_current_span("twin.reconstruction.llm_pass") as span:
        span.set_attribute("component.id", proposal.component_id)
        call = _llm_call or make_default_llm_call()
        try:
            text, provider, model = await call(llm_prompt(proposal))
        except Exception as exc:  # noqa: BLE001 - the deterministic proposal stands
            span.record_exception(exc)
            _metric("record_reconstruction_llm_pass", "unavailable")
            logger.info(
                "reconstruction_llm_unavailable",
                component_id=proposal.component_id,
                error=str(exc)[:300],
            )
            return mark_llm_unavailable(proposal, f"the model pass did not run: {exc}")
        out = apply_llm_pass(proposal, text, provider=provider, model=model)
        _metric("record_reconstruction_llm_pass", out.llm.outcome)
        span.set_attribute("reconstruction.llm_outcome", out.llm.outcome)
        span.set_attribute("reconstruction.llm_suggestions", out.llm.suggestions)
        return out


# ---------------------------------------------------------------------------
# Keys and payloads
# ---------------------------------------------------------------------------


def _stem(component_id: str) -> str:
    raw = component_id[4:] if component_id.upper().startswith("CMP-") else component_id
    stem = re.sub(r"[^A-Z0-9]+", "-", raw.upper()).strip("-")[:60].strip("-")
    return stem or "PART"


def proposal_key(component_id: str) -> str:
    """``CMP-base-plate`` -> ``FHP-BASE-PLATE``: one proposal item per component."""
    return f"FHP-{_stem(component_id)}"


def sketch_key(component_id: str, body_id: str) -> str:
    """The parametric sketch an accepted body becomes (``PSK-REC-BASE-PLATE``)."""
    extra = "" if body_id == "body" else "-" + re.sub(r"[^A-Z0-9]+", "-", body_id.upper())
    return (PSK_PREFIX + _stem(component_id) + extra)[:80].rstrip("-")


def operation_id_for(project_id: str) -> str:
    return f"job:reconstruction-{project_id}"


def _pid(project_id: str | UUID) -> UUID:
    try:
        return project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    except ValueError as exc:
        raise ReconstructionError(f"project id {project_id!r} is not a UUID", 400) from exc


def _ref(meta: dict[str, Any]) -> str | None:
    key, rev = meta.get("item_key"), meta.get("item_revision")
    return f"{key}@{rev}" if key and rev else None


def _wp_type(wp: Any) -> str:
    t = getattr(wp, "type", None)
    return str(getattr(t, "value", t) or "")


class ReconstructionProposeRequest(BaseModel):
    """Propose a feature history for the accepted concept's components."""

    #: Only these components (``CMP-...``); all made components when empty.
    component_ids: list[str] = Field(default_factory=list, max_length=60)
    #: Run the optional model pass (names, order, missing features; never numbers).
    use_llm: bool = False


class ReconstructionAcceptRequest(BaseModel):
    """Accept (and edit) proposed features of one component."""

    feature_ids: list[str] = Field(min_length=1, max_length=100)
    #: ``{feature_id: {parameter: value}}``; each edit becomes confirmed.
    edits: dict[str, dict[str, float | str]] = Field(default_factory=dict)
    #: ``{question_id: option_id}`` for the just-in-time questions.
    choices: dict[str, str] = Field(default_factory=dict)
    #: The proposal revision the person saw; ``409`` when it moved on.
    base_revision: int | None = Field(default=None, ge=1)
    accepted_by: str | None = Field(default=None, max_length=200)


class FeatureHistoryProposalView(BaseModel):
    component_id: str
    component_name: str
    item_key: str
    revision: int | None = None
    item_ref: str | None = None
    node_id: str
    status: str
    #: ``current`` or ``stale`` (a layout, sketch or context it read moved on).
    staleness: str = "current"
    open_questions: int = 0
    proposal: dict[str, Any]


class ReconstructionSkipped(BaseModel):
    component_id: str
    name: str
    reason: str


class ReconstructionProposeResponse(BaseModel):
    project_id: str
    operation_id: str
    proposals: list[FeatureHistoryProposalView] = Field(default_factory=list)
    reused: list[str] = Field(default_factory=list)
    skipped: list[ReconstructionSkipped] = Field(default_factory=list)
    summary: str = ""


class ReconstructionComponent(BaseModel):
    component_id: str
    name: str
    source: str = "made"
    proposal: FeatureHistoryProposalView | None = None


class ReconstructionView(BaseModel):
    project_id: str
    #: A concept assembly has been accepted (recorded with its components).
    concept_accepted: bool
    assembly_decision_id: str | None = None
    components: list[ReconstructionComponent] = Field(default_factory=list)
    #: ``We'd build this as: ...`` over the current proposals.
    summary: str = ""
    #: Every component with a proposal has had features accepted.
    accepted: bool = False


class ReconstructionSketch(BaseModel):
    feature_id: str
    item_key: str | None = None
    revision: int | None = None
    item_ref: str | None = None
    node_id: str
    approval_id: str
    approved: bool = False
    required_input: list[str] = Field(default_factory=list)
    estimated_parameters: list[str] = Field(default_factory=list)
    pending_features: list[str] = Field(default_factory=list)


class ReconstructionAcceptResponse(BaseModel):
    project_id: str
    operation_id: str
    proposal: FeatureHistoryProposalView
    accepted: list[str]
    rejected: list[str]
    added_dependencies: list[str] = Field(default_factory=list)
    sketches: list[ReconstructionSketch] = Field(default_factory=list)
    #: Accepted features already editable in an existing parametric sketch.
    existing: dict[str, str] = Field(default_factory=dict)
    process_written: str | None = None


# ---------------------------------------------------------------------------
# Gathering the inputs
# ---------------------------------------------------------------------------


class _Dep:
    """One record a proposal depends on."""

    def __init__(
        self,
        node_id: Any,
        role: str,
        *,
        item_key: str | None = None,
        revision: int | None = None,
        item_type: str = "",
    ) -> None:
        self.node_id = str(node_id)
        self.role = role
        self.item_key = item_key
        self.revision = revision
        self.item_type = item_type

    def to_json(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "role": self.role,
            "item_key": self.item_key,
            "revision": self.revision,
            "item_type": self.item_type,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> _Dep:
        return cls(
            raw["node_id"],
            str(raw.get("role") or ""),
            item_key=raw.get("item_key"),
            revision=raw.get("revision"),
            item_type=str(raw.get("item_type") or ""),
        )


def _dep_of(wp: Any, role: str) -> _Dep:
    meta = dict(getattr(wp, "metadata", None) or {})
    rev = meta.get("item_revision")
    return _Dep(
        wp.id,
        role,
        item_key=meta.get("item_key") if isinstance(rev, int) else None,
        revision=rev if isinstance(rev, int) else None,
        item_type=_wp_type(wp),
    )


async def _list(twin: Any, wp_type: Any, pid: UUID) -> list[Any]:
    try:
        return list(await twin.list_work_products(work_product_type=wp_type, project_id=pid))
    except Exception as exc:  # noqa: BLE001 - no list API reads as nothing
        logger.debug("reconstruction_list_failed", type=str(wp_type), error=str(exc))
        return []


def _created(wp: Any) -> str:
    stamp = getattr(wp, "created_at", None)
    return stamp.isoformat() if stamp is not None else ""


def _heads(nodes: list[Any]) -> list[Any]:
    """Only each item's newest revision (nodes without an item are kept)."""
    best: dict[str, Any] = {}
    out: list[Any] = []
    for wp in nodes:
        meta = getattr(wp, "metadata", None) or {}
        key = meta.get("item_key")
        if not key:
            out.append(wp)
            continue
        prior = best.get(str(key))
        if prior is None or int(meta.get("item_revision") or 0) > int(
            (prior.metadata or {}).get("item_revision") or 0
        ):
            best[str(key)] = wp
    return out + list(best.values())


async def _layout(twin: Any, pid: UUID) -> tuple[dict[str, Any] | None, Any | None, Any | None]:
    """``(layout, layout node, structured mesh node)``: the newest L1 layout."""
    from twin_core.models.enums import WorkProductType
    from twin_core.representation import STRUCTURED_MESH_TOOL

    meshes = sorted(
        (
            r
            for r in await _list(twin, WorkProductType.RENDER, pid)
            if (r.metadata or {}).get("generated_by") == STRUCTURED_MESH_TOOL
        ),
        key=_created,
        reverse=True,
    )
    for mesh in meshes:
        raw = (mesh.metadata or {}).get("source_layout_node_id")
        if not raw:
            continue
        try:
            node = await twin.get_work_product(UUID(str(raw)))
        except Exception:  # noqa: BLE001
            node = None
        layout = (getattr(node, "metadata", None) or {}).get("concept_layout") if node else None
        if isinstance(layout, dict):
            return layout, node, mesh
    sketches = sorted(
        (
            s
            for s in await _list(twin, WorkProductType.DESIGN_SKETCH, pid)
            if isinstance((s.metadata or {}).get("concept_layout"), dict)
        ),
        key=_created,
        reverse=True,
    )
    if sketches:
        return sketches[0].metadata["concept_layout"], sketches[0], None
    return None, None, None


async def _segmented_extents(twin: Any, pid: UUID) -> tuple[dict[str, dict[str, Any]], Any | None]:
    """Per component, the box of its part in the accepted concept in parts (estimated)."""
    from api_gateway.twin.concept_parts import part_extents_mm, segmented_heads
    from twin_core.models.enums import WorkProductType

    heads = segmented_heads(await _list(twin, WorkProductType.RENDER, pid))
    if not heads:
        return {}, None
    return part_extents_mm(heads[0]), heads[0]


async def _context(twin: Any, pid: UUID, name: str, cid: str) -> tuple[ContextFacts, Any]:
    """The component's manufacturing context (or the product's), as reconstruction reads it."""
    from api_gateway.twin.dfm_findings import process_context
    from api_gateway.twin.manufacturing_context import (
        ManufacturingContextError,
        effective_context,
    )

    stored = None
    for key in (name, cid):
        try:
            stored = await effective_context(twin, pid, key)
        except ManufacturingContextError:
            continue
        if stored is not None:
            break
    if stored is None:
        return ContextFacts(), None
    pc = process_context(stored)
    record = stored.record

    def text(field: str) -> str | None:
        entry = record.get(field)
        return str(entry.value) if entry.known and not entry.stale else None

    volume: tuple[float, float, float] | None = None
    volume_source = ""
    cap = pc.machine.capabilities.get("build_volume_mm") if pc.machine else None
    if cap is not None and isinstance(cap.value, list | tuple) and len(cap.value) == 3:
        try:
            volume = (float(cap.value[0]), float(cap.value[1]), float(cap.value[2]))
            volume_source = cap.source
        except (TypeError, ValueError):
            volume = None
    facts = ContextFacts(
        process=pc.process,
        process_authority=pc.process_authority,
        material=pc.material,
        material_authority=pc.material_authority,
        intended_use=text("intended_use"),
        production_goal=text("production_goal"),
        fastening=text("fastening"),
        context_ref=stored.ref,
        build_volume_mm=volume,
        build_volume_source=volume_source,
    )
    return facts, stored


def _reference_facts(pid: UUID, name: str, cid: str) -> tuple[list[FactInput], set[str]]:
    """Active Create references' facts that concern this component or the product."""
    from twin_core.component_identity import name_key

    try:
        from api_gateway.create.routes import get_create_store

        creation = get_create_store().get(str(pid))
    except Exception as exc:  # noqa: BLE001 - no Create record reads as no references
        logger.debug("reconstruction_references_unavailable", error=str(exc))
        return [], set()
    facts: list[FactInput] = []
    about_ids: set[str] = set()
    wanted = {cid, name, name_key(name)}
    for rec in creation.brief.reference_details:
        if not rec.active:
            continue
        components = [a for a in rec.associations if a.kind == "component"]
        about = any(a.id in wanted or name_key(a.label) == name_key(name) for a in components)
        if components and not about:
            continue  # about another component
        if about:
            about_ids.add(rec.work_product_id)
        for fact in rec.facts:
            facts.append(
                FactInput(
                    reference_id=rec.work_product_id,
                    role=rec.role,
                    parameter=fact.parameter,
                    value=fact.value,
                    unit=fact.unit,
                    basis=fact.basis,
                    region=fact.citation.region,
                    text=fact.text,
                    about_component=about,
                )
            )
    return facts, about_ids


def _connections(
    layout: dict[str, Any] | None, assembly: dict[str, Any], name: str
) -> list[dict[str, str]]:
    from twin_core.component_identity import name_key

    me = name_key(name)
    out: list[dict[str, str]] = []
    for c in (layout or {}).get("connections") or []:
        if isinstance(c, dict) and me in (name_key(c.get("a", "")), name_key(c.get("b", ""))):
            other = c.get("b") if name_key(c.get("a", "")) == me else c.get("a")
            out.append({"other": str(other or ""), "joint": str(c.get("joint") or "")})
    for c in assembly.get("connections") or []:
        parts = c.get("parts") if isinstance(c, dict) else None
        if isinstance(parts, list) and me in [name_key(str(p)) for p in parts]:
            others = [str(p) for p in parts if name_key(str(p)) != me]
            out.append({"other": others[0] if others else "", "joint": str(c.get("joint") or "")})
    return out


async def _sketches(twin: Any, pid: UUID, cid: str) -> tuple[list[tuple[str, Any]], list[Any]]:
    """``([(PSK ref, ParametricSketch)], [every SKT/SKI/PSK head bound to the component])``."""
    from api_gateway.twin.exact_cad import load_parametric_sketch
    from twin_core.design_ir.sketch_compiler import ParametricSketch
    from twin_core.models.enums import WorkProductType
    from twin_core.representation import COMPONENT_ID_KEY

    psks: list[tuple[str, Any]] = []
    nodes: list[Any] = []
    for wp_type in (
        WorkProductType.SKETCH_STROKES,
        WorkProductType.SKETCH_INTERPRETATION,
        WorkProductType.PARAMETRIC_SKETCH,
    ):
        for wp in _heads(await _list(twin, wp_type, pid)):
            meta = dict(wp.metadata or {})
            if meta.get(COMPONENT_ID_KEY) != cid:
                continue
            nodes.append(wp)
            if wp_type is WorkProductType.PARAMETRIC_SKETCH:
                try:
                    sketch = ParametricSketch.model_validate(load_parametric_sketch(meta))
                except Exception as exc:  # noqa: BLE001 - an unreadable sketch is skipped
                    logger.info(
                        "reconstruction_sketch_unreadable", node_id=str(wp.id), error=str(exc)
                    )
                    continue
                psks.append((_ref(meta) or f"node:{wp.id}", sketch))
    return psks, nodes


# ---------------------------------------------------------------------------
# Storing a proposal revision
# ---------------------------------------------------------------------------

_locks: dict[str, asyncio.Lock] = {}


def _lock(pid: UUID) -> asyncio.Lock:
    lock = _locks.get(str(pid))
    if lock is None:
        lock = _locks[str(pid)] = asyncio.Lock()
    return lock


async def _write_proposal(
    twin: Any,
    pid: UUID,
    proposal: FeatureHistoryProposal,
    deps: list[_Dep],
    *,
    change_reason: str,
    base_revision: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Write the next revision of ``FHP-<component>``, pinned to the item revisions it read."""
    from twin_core.consistency.record_pins import PINS_KEY, ItemPin
    from twin_core.models.enums import EdgeType, WorkProductType
    from twin_core.models.work_product import WorkProduct

    key = proposal_key(proposal.component_id)
    name = f"Feature history (proposed): {proposal.component_name}"
    plan = await plan_definition_revision(
        twin,
        item_type=ITEM_TYPE,
        name=name,
        project_id=pid,
        default_author="reconstruction",
        item_key=base_revision or key,
        change_reason=change_reason,
    )
    content = proposal.model_dump(mode="json")
    blob = json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    content_hash = hashlib.sha256(blob).hexdigest()
    pins = [
        ItemPin(d.item_key, int(d.revision), UUID(d.node_id), d.item_type)
        for d in deps
        if d.item_key and d.revision is not None
    ]
    meta: dict[str, Any] = {
        META_KEY: content,
        "component_ref": proposal.component_id,
        "component_name": proposal.component_name,
        "status": proposal.status,
        "inputs_sha256": proposal.inputs_sha256,
        "authority": "proposal",
        "estimated": True,
        "content_sha256": content_hash,
        "depends_on_inputs": [d.to_json() for d in deps],
        PINS_KEY: [p.to_json() for p in pins],
    }
    if plan is not None:
        meta.update(plan.stamp())
    now = datetime.now(UTC)
    wp_id = uuid4()
    created = await twin.create_work_product(
        WorkProduct(
            id=wp_id,
            name=name,
            type=WorkProductType.FEATURE_HISTORY_PROPOSAL,
            domain="mechanical",
            file_path="",
            content_hash=content_hash,
            format="json",
            metadata=meta,
            created_at=now,
            updated_at=now,
            created_by="reconstruction",
            project_id=pid,
        )
    )
    node_id = getattr(created, "id", wp_id)
    result: dict[str, Any] = {}
    await finish_definition_revision(twin, plan, node_id, name=name, result=result)
    for dep in deps:
        try:
            await twin.add_edge(
                node_id,
                UUID(dep.node_id),
                EdgeType.DEPENDS_ON,
                metadata={
                    "kind": EDGE_KIND,
                    "role": dep.role,
                    "item_ref": f"{dep.item_key}@{dep.revision}" if dep.item_key else None,
                },
            )
        except Exception as exc:  # noqa: BLE001 - the revision is saved; the edge is reported
            logger.warning("reconstruction_edge_failed", node_id=str(node_id), error=str(exc))
    stored = await twin.get_work_product(node_id) or created
    return stored, result


def _view(wp: Any, *, result: dict[str, Any] | None = None) -> FeatureHistoryProposalView:
    from twin_core.consistency.record_pins import record_staleness

    meta = dict(wp.metadata or {})
    content = dict(meta.get(META_KEY) or {})
    key = (result or {}).get("item_key") or meta.get("item_key") or ""
    rev = (result or {}).get("revision") or meta.get("item_revision")
    questions = [a for a in content.get("ambiguities") or [] if not a.get("choice")]
    return FeatureHistoryProposalView(
        component_id=str(content.get("component_id") or meta.get("component_ref") or ""),
        component_name=str(content.get("component_name") or ""),
        item_key=str(key),
        revision=rev,
        item_ref=f"{key}@{rev}" if key and rev else None,
        node_id=str(wp.id),
        status=str(content.get("status") or meta.get("status") or "proposed"),
        staleness=record_staleness(meta),
        open_questions=len(questions),
        proposal=content,
    )


async def _head(twin: Any, pid: UUID, key: str) -> tuple[Any, Any] | None:
    from twin_core.items import find_item

    item = await find_item(twin, key, pid)
    if item is None or item.head_node_id is None or item.item_type != ITEM_TYPE:
        return None
    node = await twin.get_work_product(item.head_node_id)
    return (item, node) if node is not None else None


def _publish(project_id: str, event: str, state: str, **fields: Any) -> None:
    from api_gateway.operations import publisher as operations

    operations.publish(
        project_id,
        operation_id_for(project_id),
        event,
        state,
        event_id=f"reconstruction-{project_id}-{event}-{uuid4().hex[:8]}",
        domain="mechanical",
        **fields,
    )


# ---------------------------------------------------------------------------
# Propose
# ---------------------------------------------------------------------------


async def propose(
    twin: Any, project_id: str, request: ReconstructionProposeRequest
) -> ReconstructionProposeResponse:
    """Propose (or reuse) a feature history for each made component of the accepted concept."""
    from twin_core.component_identity import match_name, project_identity

    pid = _pid(project_id)
    with tracer.start_as_current_span("twin.reconstruction.propose") as span:
        span.set_attribute("project_id", str(pid))
        span.set_attribute("reconstruction.use_llm", request.use_llm)
        ident = await project_identity(twin, pid)
        decision_id = ident.get("current_decision_id")
        if not ident["components"] or not decision_id:
            _metric("record_reconstruction_proposal", "refused")
            raise ReconstructionError(
                "there is no accepted concept yet: accept a concept (its parts are recorded "
                "with it) before making it editable"
            )
        try:
            out = await _propose_all(twin, pid, ident, decision_id, request, match_name)
        except ReconstructionError:
            raise
        except Exception as exc:
            span.record_exception(exc)
            _metric("record_reconstruction_proposal", "failed")
            logger.warning("reconstruction_failed", project_id=str(pid), error=str(exc))
            _publish(
                str(pid),
                "failed",
                "failed",
                title="Could not propose how to build it",
                message=f"The feature history could not be proposed: {str(exc)[:200]}",
            )
            raise
        span.set_attribute("reconstruction.proposals", len(out.proposals))
    if out.proposals:
        _publish(
            str(pid),
            "preview_ready",
            "waiting_for_user",
            title="Ready to make it editable",
            stage_id="reconstruction_proposed",
            stage_title="Proposed",
            message=out.summary or "A feature history is proposed.",
            requires_action=True,
            artifact_refs=[
                {
                    "kind": "work_product",
                    "id": p.node_id,
                    "label": f"Proposed features {p.item_ref or p.item_key}",
                    "authority": "preview",
                }
                for p in out.proposals[:10]
            ],
        )
    return out


async def _propose_all(
    twin: Any,
    pid: UUID,
    ident: dict[str, Any],
    decision_id: str,
    request: ReconstructionProposeRequest,
    match_name: Any,
) -> ReconstructionProposeResponse:
    async with _lock(pid):
        decision = None
        try:
            decision = await twin.get_work_product(UUID(str(decision_id)))
        except Exception:  # noqa: BLE001
            decision = None
        assembly = dict(((getattr(decision, "metadata", None) or {}).get("assembly")) or {})
        layout, layout_node, mesh_node = await _layout(twin, pid)
        layout_ref = _ref(dict(layout_node.metadata or {})) if layout_node is not None else None
        if layout_node is not None and layout_ref is None:
            layout_ref = f"node:{layout_node.id}"
        parts = [p for p in (layout or {}).get("parts") or [] if isinstance(p, dict)]
        seg_extents, seg_node = await _segmented_extents(twin, pid)
        current = {cid: info for cid, info in ident["components"].items() if info.get("in_current")}
        made = {cid: info for cid, info in current.items() if info.get("source") == "made"}
        wanted = request.component_ids or list(current)
        response = ReconstructionProposeResponse(
            project_id=str(pid), operation_id=operation_id_for(str(pid))
        )
        for cid in wanted:
            info = current.get(cid)
            if info is None:
                response.skipped.append(
                    ReconstructionSkipped(
                        component_id=cid, name=cid, reason="not a part of the accepted concept"
                    )
                )
                continue
            name = str(info["name"])
            if info.get("source") != "made":
                response.skipped.append(
                    ReconstructionSkipped(
                        component_id=cid,
                        name=name,
                        reason=f"a {info.get('source')} part: it is bought, not designed",
                    )
                )
                continue
            part = next(
                (p for p in parts if match_name(str(p.get("name") or ""), {name: cid}) == cid),
                None,
            )
            part_ref = layout_ref if part is not None else None
            from_segment = False
            if part is None and cid in seg_extents and seg_node is not None:
                # No layout block: the accepted concept's part a person mapped
                # to this component, its box read as an estimate (never confirmed).
                part = dict(seg_extents[cid], name=name)
                part_ref = f"node:{seg_node.id}"
                from_segment = True
            psks, sketch_nodes = await _sketches(twin, pid, cid)
            context, stored_context = await _context(twin, pid, name, cid)
            facts, about_ids = _reference_facts(pid, name, cid)
            inputs = ReconstructionInputs(
                component_id=cid,
                component_name=name,
                part=part,
                layout_ref=part_ref,
                sketches=psks,
                context=context,
                facts=facts,
                connections=_connections(layout, assembly, name),
                only_component=len(made) == 1,
            )
            digest = inputs_hash(inputs, use_llm=request.use_llm)
            head = await _head(twin, pid, proposal_key(cid))
            if head is not None:
                hmeta = dict(head[1].metadata or {})
                if hmeta.get("inputs_sha256") == digest and hmeta.get("status") == "proposed":
                    response.proposals.append(_view(head[1]))
                    response.reused.append(cid)
                    _metric("record_reconstruction_proposal", "reused")
                    continue
            proposal = deterministic_proposal(inputs)
            if request.use_llm:
                proposal = await _llm_pass(proposal)
            used_refs = {
                str(prov.ref).split(" (")[0]
                for f in proposal.features
                for prov in f.provenance
                if prov.kind == "reference_fact" and prov.ref
            }
            for f in proposal.features:
                for param in f.parameters.values():
                    if param.source and param.source.kind == "reference_fact" and param.source.ref:
                        used_refs.add(str(param.source.ref).split(" (")[0])
            ref_ids = sorted(about_ids | used_refs)
            proposal.inputs = ProposalInputs(
                assembly_decision_id=str(decision_id),
                layout_ref=inputs.layout_ref,
                layout_node_id=(
                    str(layout_node.id)
                    if layout_node is not None and part and not from_segment
                    else None
                ),
                mesh_node_id=(
                    str(seg_node.id)
                    if from_segment and seg_node is not None
                    else str(mesh_node.id)
                    if mesh_node is not None and part
                    else None
                ),
                reference_ids=ref_ids,
                sketch_refs=[_ref(dict(n.metadata or {})) or f"node:{n.id}" for n in sketch_nodes],
                context_ref=context.context_ref,
            )
            proposal.inputs_sha256 = digest
            deps: list[_Dep] = []
            if decision is not None:
                deps.append(_Dep(decision.id, "concept_assembly", item_type="design_decision"))
            if from_segment and seg_node is not None:
                deps.append(_Dep(seg_node.id, "l0_segmented_part", item_type="render"))
            elif part is not None and layout_node is not None:
                deps.append(_dep_of(layout_node, "l1_layout"))
                if mesh_node is not None:
                    deps.append(_Dep(mesh_node.id, "l1_mesh", item_type="render"))
            for rid in ref_ids:
                try:
                    if await twin.get_work_product(UUID(rid)) is not None:
                        deps.append(_Dep(rid, "reference", item_type="reference"))
                except (ValueError, TypeError):
                    continue
            deps += [_dep_of(n, "sketch") for n in sketch_nodes]
            if stored_context is not None:
                deps.append(
                    _Dep(
                        stored_context.node_id,
                        "manufacturing_context",
                        item_key=stored_context.item_key,
                        revision=stored_context.revision,
                        item_type="manufacturing_context",
                    )
                )
            wp, result = await _write_proposal(
                twin, pid, proposal, deps, change_reason="feature history proposed"
            )
            view = _view(wp, result=result)
            response.proposals.append(view)
            _metric("record_reconstruction_proposal", "proposed")
            logger.info(
                "reconstruction_proposed",
                project_id=str(pid),
                component_id=cid,
                item_ref=view.item_ref,
                features=len(proposal.features),
                questions=len(proposal.ambiguities),
                llm=proposal.llm.outcome,
                profile=proposal.dfm_strategy.profile_id,
            )
        response.summary = build_summary([p.proposal for p in response.proposals])
        return response


def build_summary(proposals: list[dict[str, Any]]) -> str:
    """``We'd build this as: the base: a round disc; the shade: a round rod, hollowed out``."""
    parts = [str(p.get("summary") or "") for p in proposals if p.get("summary")]
    return ("We'd build this as: " + "; ".join(parts) + ".") if parts else ""


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def reconstruction_view(twin: Any, project_id: str) -> ReconstructionView:
    """The latest proposal per component of the accepted concept."""
    from twin_core.component_identity import project_identity

    pid = _pid(project_id)
    with tracer.start_as_current_span("twin.reconstruction.view") as span:
        ident = await project_identity(twin, pid)
        components: list[ReconstructionComponent] = []
        for cid, info in ident["components"].items():
            if not info.get("in_current"):
                continue
            head = await _head(twin, pid, proposal_key(cid))
            components.append(
                ReconstructionComponent(
                    component_id=cid,
                    name=str(info["name"]),
                    source=str(info.get("source") or "made"),
                    proposal=_view(head[1]) if head is not None else None,
                )
            )
        with_proposal = [c.proposal for c in components if c.proposal is not None]
        span.set_attribute("reconstruction.proposals", len(with_proposal))
        return ReconstructionView(
            project_id=str(pid),
            concept_accepted=bool(ident["components"]) and bool(ident.get("current_decision_id")),
            assembly_decision_id=ident.get("current_decision_id"),
            components=components,
            summary=build_summary([p.proposal for p in with_proposal]),
            accepted=bool(with_proposal) and all(p.status != "proposed" for p in with_proposal),
        )


# ---------------------------------------------------------------------------
# Accept
# ---------------------------------------------------------------------------


async def accept_proposal(
    twin: Any, project_id: str, key: str, request: ReconstructionAcceptRequest
) -> ReconstructionAcceptResponse:
    """Accept features of one proposal: parametric sketches held for approval."""
    from api_gateway.twin.sketch_compiler_recorder import (
        PARAMETRIC_AUTHORITY,
        PARAMETRIC_FILE,
        PARAMETRIC_TYPE,
        _write,
        render_primitives_svg,
    )
    from twin_core.items import parse_item_ref
    from twin_core.models.enums import EdgeType, WorkProductType
    from twin_core.representation import COMPONENT_ID_KEY

    pid = _pid(project_id)
    try:
        item_key, _pin = parse_item_ref(key)
    except ValueError as exc:
        raise ReconstructionError(str(exc), 400) from exc
    with tracer.start_as_current_span("twin.reconstruction.accept") as span:
        span.set_attribute("project_id", str(pid))
        span.set_attribute("reconstruction.item_key", item_key)
        async with _lock(pid):
            head = await _head(twin, pid, item_key)
            if head is None:
                raise ReconstructionError(f"no feature history proposal {item_key}", 404)
            item, node = head
            ref = f"{item.key}@{item.head_revision}"
            if request.base_revision is not None and request.base_revision != item.head_revision:
                _metric("record_reconstruction_proposal", "refused")
                raise ReconstructionError(
                    f"{item.key} is at @{item.head_revision}, not @{request.base_revision}: "
                    "review the current proposal"
                )
            meta = dict(node.metadata or {})
            proposal = FeatureHistoryProposal.model_validate(meta.get(META_KEY) or {})
            if proposal.status != "proposed":
                _metric("record_reconstruction_proposal", "refused")
                raise ReconstructionError(f"{ref} was already accepted; propose again to change it")
            try:
                plan = accept_features(
                    proposal,
                    request.feature_ids,
                    edits={k: dict(v) for k, v in request.edits.items()},
                    choices=dict(request.choices),
                )
            except AcceptanceError as exc:
                _metric("record_reconstruction_proposal", "refused")
                raise ReconstructionError(str(exc), exc.status) from exc

            sketches: list[ReconstructionSketch] = []
            for todo in plan.sketches:
                parametric = todo.sketch
                content = parametric.model_dump(mode="json")
                feature = plan.proposal.feature(todo.body_id)
                name = proposal.component_name + (
                    ""
                    if todo.body_id == "body"
                    else f" ({feature.name if feature else todo.body_id})"
                )
                psk, result = await _write(
                    twin,
                    pid,
                    item_type=PARAMETRIC_TYPE,
                    wp_type=WorkProductType.PARAMETRIC_SKETCH,
                    item_key=sketch_key(proposal.component_id, todo.body_id),
                    name=name,
                    change_reason=f"accepted from {ref}",
                    content=content,
                    content_file=PARAMETRIC_FILE,
                    svg=render_primitives_svg(
                        parametric.elements, title="Parametric sketch: accepted reconstruction"
                    ),
                    metadata={
                        COMPONENT_ID_KEY: proposal.component_id,
                        "parametric_sketch": content,
                        "reconstruction_ref": ref,
                        "feature_id": todo.body_id,
                        "plane_id": None,
                        "unit": parametric.unit,
                        "feature_plan": [
                            p.model_dump(mode="json") for p in parametric.feature_plan
                        ],
                        PRINCIPAL_PLACEMENT_KEY: todo.placement,
                        "estimated_parameters": todo.estimated_parameters,
                        "pending_features": todo.pending,
                        "accepted_by": request.accepted_by,
                        "operation_id": operation_id_for(str(pid)),
                        "approved": False,
                        "approved_at": None,
                        "authority": PARAMETRIC_AUTHORITY,
                    },
                    depends_on=[],
                )
                try:
                    await twin.add_edge(
                        psk.id,
                        node.id,
                        EdgeType.DEPENDS_ON,
                        metadata={"kind": EDGE_KIND, "role": "proposal", "item_ref": ref},
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "reconstruction_edge_failed", node_id=str(psk.id), error=str(exc)
                    )
                pmeta = dict(psk.metadata or {})
                psk_ref = result.get("item_ref") or _ref(pmeta)
                plan.proposal.accepted_sketches[todo.body_id] = str(psk_ref or psk.id)
                required = [
                    n
                    for item_plan in parametric.feature_plan
                    for n, p in item_plan.parameters.items()
                    if p.status == "required_input"
                ]
                sketches.append(
                    ReconstructionSketch(
                        feature_id=todo.body_id,
                        item_key=result.get("item_key") or pmeta.get("item_key"),
                        revision=result.get("revision") or pmeta.get("item_revision"),
                        item_ref=psk_ref,
                        node_id=str(psk.id),
                        approval_id=f"parametric_sketch:{psk.id}",
                        required_input=required,
                        estimated_parameters=todo.estimated_parameters,
                        pending_features=[p["feature_id"] for p in todo.pending],
                    )
                )
            for fid, existing_ref in plan.existing.items():
                plan.proposal.accepted_sketches[fid] = existing_ref
            deps = [_Dep.from_json(d) for d in meta.get("depends_on_inputs") or []]
            wp, result = await _write_proposal(
                twin,
                pid,
                plan.proposal,
                deps,
                change_reason=f"{len(plan.accepted)} feature(s) accepted",
                base_revision=ref,
            )
            view = _view(wp, result=result)
            outcome = plan.proposal.status
            _metric("record_reconstruction_proposal", outcome)
            for fid in plan.accepted:
                f = plan.proposal.feature(fid)
                if f is not None:
                    _metric("record_reconstruction_feature_accepted", f.kind, f.suggested_by)
            span.set_attribute("reconstruction.accepted", len(plan.accepted))
            span.set_attribute("reconstruction.sketches", len(sketches))
            logger.info(
                "reconstruction_accepted",
                project_id=str(pid),
                item_ref=view.item_ref,
                accepted=len(plan.accepted),
                rejected=len(plan.rejected),
                sketches=[s.item_ref for s in sketches],
            )

        process_written: str | None = None
        if plan.process_choice:
            from api_gateway.twin import manufacturing_context as mc

            try:
                await mc.write_context(
                    twin,
                    pid,
                    {
                        "selected_process": mc.stated(
                            plan.process_choice,
                            source="reconstruction",
                            actor=request.accepted_by or "",
                        )
                    },
                    source="reconstruction",
                    actor=request.accepted_by or "",
                    change_reason="process chosen while making the concept editable",
                )
                process_written = plan.process_choice
            except Exception as exc:  # noqa: BLE001 - the acceptance stands; reported
                span.record_exception(exc)
                logger.warning("reconstruction_process_write_failed", error=str(exc))

    if sketches:
        _publish(
            str(pid),
            "review_required",
            "waiting_for_user",
            title="Editable parts awaiting sign-off",
            stage_id="reconstruction_review",
            stage_title="Sign-off",
            message=(
                f"{proposal.component_name}: {len(sketches)} editable sketch(es) made from the "
                "accepted features. Approve them to build exact CAD; unconfirmed sizes are "
                "still asked for."
            ),
            target_key=item.key,
            target_revision=view.item_ref,
            base_revision=ref,
            requires_action=True,
            action_refs=[
                {"kind": "approval", "id": s.approval_id, "label": f"Approve {s.item_ref}"}
                for s in sketches
            ],
            artifact_refs=[
                {
                    "kind": "work_product",
                    "id": s.node_id,
                    "label": f"Sketch {s.item_ref}",
                    "authority": "preview",
                }
                for s in sketches
            ],
        )
    return ReconstructionAcceptResponse(
        project_id=str(pid),
        operation_id=operation_id_for(str(pid)),
        proposal=view,
        accepted=plan.accepted,
        rejected=plan.rejected,
        added_dependencies=plan.added_dependencies,
        sketches=sketches,
        existing=plan.existing,
        process_written=process_written,
    )


__all__ = [
    "ITEM_TYPE",
    "ReconstructionAcceptRequest",
    "ReconstructionAcceptResponse",
    "LlmUnavailableError",
    "ReconstructionProposeRequest",
    "ReconstructionProposeResponse",
    "ReconstructionError",
    "ReconstructionView",
    "accept_proposal",
    "build_summary",
    "make_default_llm_call",
    "proposal_key",
    "propose",
    "reconstruction_view",
    "set_llm_call",
    "sketch_key",
]
