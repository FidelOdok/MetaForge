"""The desk lamp golden path: seed the project and read its demo manifest (step 1).

The lamp is defined once, as data, in ``examples/desk_lamp/definition.json``
(the brief, references with roles, the manufacturing context, the concept
assembly and the four interface contracts). :func:`seed_desk_lamp` creates it
in the twin through the services the routes already use:

1. the project (``POST /v1/projects``' backend), named
   ``Desk lamp (golden path)``;
2. the interface contracts (``IFC-*`` items), each ``proposed``;
3. the manufacturing context (``MFC-PROJECT`` and per-component records),
   each field with its authority: what the definition states is
   ``user_stated``, what it assumes (the diffuser's translucent material) is
   an ``assumption``;
4. the concept assembly, as a design decision with an ``assembly``, so every
   part gets its ``CMP-`` component id;
5. the Create brief: items, estimated measurements and assumptions.

**Idempotent, never overwriting.** Running it again finds the project by name
and fills only what is missing: an existing contract is kept as it is (even
if someone changed it), a context field is written only while it is unknown,
the assembly is recorded only when the project has none, and a brief item,
measurement or assumption only when absent. A project with the same name that
this seeder did not create is refused (409), with nothing written.

:func:`build_manifest` lists every golden-path step as ``produced`` (with the
item revisions, record ids, run ids and links that show it) or ``missing``
(with the step that would produce it). It reads; it never fabricates.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.golden_path.desk_lamp")

EXAMPLE_ID = "desk-lamp"
#: Where the example definitions live; the image copies them to ``/app/examples``.
EXAMPLES_DIR_ENV = "METAFORGE_EXAMPLES_DIR"
#: Words in the project description that mark a project this seeder created.
SEED_MARKER = "Seeded from examples/desk_lamp"
SEED_SOURCE = "golden_path:desk-lamp"

#: What the manifest says it cannot show yet, so a reader does not assume it.
KNOWN_LIMITATIONS: tuple[str, ...] = (
    "Contract checks run when asked (Check now, or the demo runner); a change marks the "
    "last check stale through its revision pins rather than re-running it.",
    "Positions in the CAD to EDA contract are compared in the frame both sides record "
    "them in; no frame transform is applied.",
    "Assembly instructions have no producing step yet.",
    "No reference image ships with the example; a placeholder is never generated.",
)


class GoldenPathError(ValueError):
    """A request that cannot be honoured. ``status`` is the HTTP status it maps to."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class SeedStep(BaseModel):
    step: str
    #: ``created``, ``unchanged`` (already there), ``kept`` (left as someone set it).
    outcome: str
    detail: str = ""


class SeedResult(BaseModel):
    example: str = EXAMPLE_ID
    project_id: str
    name: str
    created_project: bool
    steps: list[SeedStep] = Field(default_factory=list)


def _examples_dir() -> Path:
    env = os.environ.get(EXAMPLES_DIR_ENV)
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "examples"


def definition_path() -> Path:
    return _examples_dir() / "desk_lamp" / "definition.json"


def load_definition(path: Path | None = None) -> dict[str, Any]:
    """The lamp's definition; ``GoldenPathError`` (503) when it is not installed."""
    target = path or definition_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GoldenPathError(
            f"the desk lamp example is not installed here ({target} is missing)", 503
        ) from exc
    except ValueError as exc:
        raise GoldenPathError(f"the desk lamp definition is not valid JSON: {exc}", 500) from exc
    if not isinstance(data, dict) or data.get("id") != EXAMPLE_ID:
        raise GoldenPathError("the desk lamp definition is not the desk lamp", 500)
    return data


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


async def _find_project(backend: Any, name: str) -> Any | None:
    for project in await backend.list_projects():
        if project.name == name:
            return project
    return None


async def _seed_contracts(
    twin: Any, pid: UUID, definition: dict[str, Any], actor: str, backend: Any
) -> list[SeedStep]:
    from api_gateway.twin.interface_contracts import (
        contract_from_body,
        get_contract,
        write_contract,
    )
    from twin_core.models.interface_contract import CONTRACT_KEYS

    steps = []
    for raw in definition.get("contracts") or []:
        contract = contract_from_body({**raw, "seeded_by": EXAMPLE_ID})
        key = CONTRACT_KEYS[contract.kind]
        existing = await get_contract(twin, pid, key)
        if existing is not None:
            steps.append(SeedStep(step=f"contract {key}", outcome="kept", detail=existing.ref))
            continue
        stored, _ = await write_contract(
            twin,
            pid,
            contract,
            actor=actor,
            source=SEED_SOURCE,
            change_reason="seeded from the desk lamp definition",
            project_backend=backend,
        )
        steps.append(SeedStep(step=f"contract {key}", outcome="created", detail=stored.ref))
    return steps


def _context_updates(raw: dict[str, Any], current: Any, actor: str) -> dict[str, Any]:
    """Fields to write: only those the current record does not know."""
    from twin_core.manufacturing.context import Authority, ContextField, Provenance

    updates: dict[str, Any] = {}
    for name, spec in raw.items():
        if current is not None and current.get(name).known:
            continue  # someone (or an earlier seed) set it; never overwrite
        updates[name] = ContextField(
            value=spec.get("value"),
            authority=Authority(spec.get("authority", "assumption")),
            provenance=Provenance(source=SEED_SOURCE, actor=actor, ref="examples/desk_lamp"),
            note=str(spec.get("note") or ""),
            process_specific=bool(spec.get("process_specific")),
        )
    return updates


async def _seed_context(
    twin: Any, pid: UUID, definition: dict[str, Any], actor: str, backend: Any
) -> list[SeedStep]:
    from api_gateway.twin.manufacturing_context import read_context, write_context

    section = definition.get("manufacturing_context") or {}
    scopes: list[tuple[str | None, dict[str, Any]]] = [(None, section.get("product") or {})]
    scopes += list((section.get("components") or {}).items())
    steps = []
    for component, fields in scopes:
        stored = await read_context(twin, pid, component)
        updates = _context_updates(fields, stored.record if stored else None, actor)
        label = f"manufacturing context {component or 'product'}"
        if not updates:
            steps.append(
                SeedStep(step=label, outcome="unchanged", detail=stored.ref if stored else "")
            )
            continue
        outcome = await write_context(
            twin,
            pid,
            updates,
            source=SEED_SOURCE,
            actor=actor,
            change_reason="seeded from the desk lamp definition",
            component_key=component,
            project_backend=backend,
        )
        ref = outcome.stored.ref if outcome.stored else ""
        steps.append(
            SeedStep(
                step=label,
                outcome="created" if outcome.revision_created else "unchanged",
                detail=ref + (f"; kept {len(outcome.refused)} existing" if outcome.refused else ""),
            )
        )
    return steps


async def _seed_assembly(
    twin: Any, pid: UUID, definition: dict[str, Any], backend: Any
) -> SeedStep:
    from api_gateway.twin.decision_recorder import make_decision_recorder
    from twin_core.component_identity import concept_assemblies

    if await concept_assemblies(twin, pid):
        return SeedStep(step="concept assembly", outcome="kept", detail="the project has one")
    spec = definition.get("concept_assembly") or {}
    sizes = spec.get("estimated_sizes") or {}
    size_lines = [
        f"{name}: about {' x '.join(f'{v:g}' for v in s.get('size_mm', []))} mm (estimated)"
        for name, s in sizes.items()
    ]
    rationale = str(spec.get("rationale") or "")
    if size_lines:
        rationale += " Approximate sizes, all estimated: " + "; ".join(size_lines) + "."
    recorder = make_decision_recorder(twin, backend)
    result = await recorder(
        title=str(spec.get("title") or "Desk lamp concept assembly"),
        rationale=rationale,
        project_id=str(pid),
        domain="systems",
        assembly={"parts": spec.get("parts") or [], "connections": spec.get("connections") or []},
    )
    return SeedStep(step="concept assembly", outcome="created", detail=str(result.get("node_id")))


def _seed_brief(pid: UUID, definition: dict[str, Any], actor: str) -> SeedStep:
    from api_gateway.create.models import Assumption, BriefItem, HistoryEntry, Measurement
    from api_gateway.create.routes import get_create_store

    store = get_create_store()
    current = store.get(str(pid))
    draft = current.model_copy(deep=True)
    brief = definition.get("brief") or {}
    added: list[str] = []
    for key, item in (brief.get("items") or {}).items():
        have = draft.brief.items.get(key)
        if have is not None and have.status != "missing":
            continue
        draft.brief.items[key] = BriefItem(
            value=str(item.get("value") or ""),
            status=item.get("status", "assumed"),
            source=item.get("source", "agent"),
        )
        added.append(key)
    known = {m.id for m in draft.brief.measurements}
    for raw in brief.get("measurements") or []:
        if raw.get("id") not in known:
            draft.brief.measurements.append(Measurement(**raw))
            added.append(f"measurement {raw.get('id')}")
    known = {a.id for a in draft.brief.assumptions}
    for raw in brief.get("assumptions") or []:
        if raw.get("id") not in known:
            draft.brief.assumptions.append(Assumption(**raw))
            added.append(f"assumption {raw.get('id')}")
    if not added:
        return SeedStep(step="create brief", outcome="unchanged")
    draft.history.append(
        HistoryEntry(
            actor=actor,
            surface="api",
            change="brief",
            summary="Brief seeded from the desk lamp example: " + ", ".join(added),
        )
    )
    saved = store.save(draft, expected_version=current.version)
    return SeedStep(
        step="create brief",
        outcome="created",
        detail=f"version {saved.version}: {len(added)} added",
    )


async def seed_desk_lamp(
    twin: Any,
    project_backend: Any,
    *,
    actor: str,
    definition: dict[str, Any] | None = None,
) -> SeedResult:
    """Create (or complete) the desk lamp project. Idempotent; never overwrites."""
    spec = definition or load_definition()
    name = str(spec["project"]["name"])
    with tracer.start_as_current_span("golden_path.desk_lamp.seed") as span:
        span.set_attribute("golden_path", EXAMPLE_ID)
        existing = await _find_project(project_backend, name)
        if existing is not None and SEED_MARKER not in (existing.description or ""):
            raise GoldenPathError(
                f"a project named {name!r} already exists and was not created from the desk "
                "lamp example; nothing was changed. Rename it to seed the example.",
                409,
            )
        created = existing is None
        if existing is None:
            try:
                existing = await project_backend.create_project(
                    name=name, description=str(spec["project"]["description"]), status="draft"
                )
            except ValueError as exc:  # lost a race with another seed
                existing = await _find_project(project_backend, name)
                if existing is None:
                    raise GoldenPathError(str(exc), 409) from exc
                created = False
        pid = UUID(str(existing.id))
        span.set_attribute("project_id", str(pid))
        result = SeedResult(project_id=str(pid), name=name, created_project=created)
        result.steps.append(
            SeedStep(step="project", outcome="created" if created else "unchanged", detail=str(pid))
        )
        result.steps += await _seed_contracts(twin, pid, spec, actor, project_backend)
        result.steps += await _seed_context(twin, pid, spec, actor, project_backend)
        result.steps.append(await _seed_assembly(twin, pid, spec, project_backend))
        result.steps.append(_seed_brief(pid, spec, actor))
        logger.info(
            "golden_path_seeded",
            example=EXAMPLE_ID,
            project_id=str(pid),
            created_project=created,
            steps={s.step: s.outcome for s in result.steps},
        )
        return result


# ---------------------------------------------------------------------------
# The demo manifest
# ---------------------------------------------------------------------------


def _run_id_of(meta: dict[str, Any]) -> str | None:
    for key in ("run_id", "recorded_in_run", "design_flow_run_id"):
        if meta.get(key):
            return str(meta[key])
    return None


async def _step_status(
    twin: Any, pid: UUID, step: dict[str, Any], items: list[Any]
) -> dict[str, Any]:
    from twin_core.impact.cross_domain import producer_tool
    from twin_core.models.enums import WorkProductType

    found: list[dict[str, Any]] = []
    types = set(step.get("item_types") or [])
    states = set(step.get("geometry_states") or [])
    for item in items:
        if item.item_type not in types or item.head_node_id is None:
            continue
        node = await twin.graph.get_node(item.head_node_id)
        meta = dict(getattr(node, "metadata", None) or {})
        if states and str(meta.get("geometry_state") or "") not in states:
            continue
        found.append(
            {
                "ref": f"{item.key}@{item.head_revision}",
                "node_id": str(item.head_node_id),
                "link": f"/v1/twin/nodes/{item.head_node_id}",
                "run_id": _run_id_of(meta),
            }
        )
    for record_type in step.get("record_types") or []:
        for node in await twin.list_work_products(
            work_product_type=WorkProductType(record_type), project_id=pid
        ):
            meta = dict(node.metadata or {})
            found.append(
                {
                    "ref": f"record:{node.id}",
                    "node_id": str(node.id),
                    "link": f"/v1/twin/nodes/{node.id}",
                    "run_id": _run_id_of(meta),
                    "staleness": meta.get("staleness", "current"),
                }
            )
    tools = set(step.get("evidence_tools") or [])
    if tools:
        for entity in await twin.list_engineering_entities(project_id=pid, entity_type="evidence"):
            meta = dict(entity.metadata or {})
            if producer_tool(meta) in tools:
                found.append(
                    {
                        "ref": f"record:{entity.id}",
                        "node_id": str(entity.id),
                        "tool": producer_tool(meta),
                        "run_id": _run_id_of(meta),
                        "staleness": meta.get("staleness", "current"),
                    }
                )
    if step.get("baselines"):
        for baseline in await twin.list_baselines(project_id=pid):
            found.append({"ref": f"baseline:{baseline.id}", "node_id": str(baseline.id)})
    out: dict[str, Any] = {
        "id": step["id"],
        "label": step.get("label", step["id"]),
        "status": "produced" if found else "missing",
        "artifacts": found,
    }
    if not found:
        out["produced_by"] = step.get("produced_by", "")
    return out


async def build_manifest(
    twin: Any,
    project_id: UUID | str,
    *,
    definition: dict[str, Any] | None = None,
    run_id: str | None = None,
    check_records: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Every golden-path step as produced or missing, and each contract's status."""
    from api_gateway.twin.interface_contracts import contracts_view
    from twin_core.items import list_items

    spec = definition or load_definition()
    pid = project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    with tracer.start_as_current_span("golden_path.desk_lamp.manifest") as span:
        span.set_attribute("project_id", str(pid))
        items = await list_items(twin, project_id=pid)
        steps = [await _step_status(twin, pid, s, items) for s in spec.get("steps") or []]
        view = await contracts_view(twin, pid)
        contracts = []
        for c in view["contracts"]:
            latest = c.get("latest_check") or {}
            contracts.append(
                {
                    "key": c["key"],
                    "ref": c["ref"],
                    "interface_version": c["interface_version"],
                    "status": c["status"],
                    "create_line": c["create_line"],
                    "awaiting_agreement": c["awaiting_agreement"],
                    "check_record_id": latest.get("record_id"),
                    "check_outcome": latest.get("outcome"),
                    "check_stale": latest.get("stale"),
                    "not_evaluated": [
                        {"check": ch["id"], "missing": ch["missing"]}
                        for ch in latest.get("checks") or []
                        if ch["outcome"] == "not_evaluated"
                    ],
                }
            )
        run_ids = sorted(
            {a["run_id"] for s in steps for a in s["artifacts"] if a.get("run_id")}
            | set((check_records or {}).values())
        )
        missing = [
            {"step": s["id"], "label": s["label"], "produced_by": s.get("produced_by", "")}
            for s in steps
            if s["status"] == "missing"
        ]
        manifest: dict[str, Any] = {
            "golden_path": EXAMPLE_ID,
            "definition_version": spec.get("version"),
            "project_id": str(pid),
            "run_id": run_id or str(uuid4()),
            "generated_at": datetime.now(UTC).isoformat(),
            "contracts": contracts,
            "steps": steps,
            "missing": missing,
            "run_ids": run_ids,
            "check_records": dict(check_records or {}),
            "known_limitations": list(KNOWN_LIMITATIONS),
        }
        span.set_attribute("manifest.missing", len(missing))
        logger.info(
            "golden_path_manifest_built",
            example=EXAMPLE_ID,
            project_id=str(pid),
            produced=[s["id"] for s in steps if s["status"] == "produced"],
            missing=[m["step"] for m in missing],
        )
        return manifest
