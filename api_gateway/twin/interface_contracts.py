"""Interface contracts in the twin: store, link, agree, check (lamp golden path, step 1).

A contract (:class:`~twin_core.models.interface_contract.InterfaceContract`) is
a definition item, ``IFC-CAD-EDA``, ``IFC-EDA-FIRMWARE``, ``IFC-SIMULATION`` or
``IFC-MANUFACTURING``. Every write that changes it is its next revision; the
*interface version* moves only when the coupled parameters, the owning
domains or the expected artifacts change, so linking an item keeps the
agreements made on the same version.

* **Links.** Once an item that implements a side exists, :func:`link_item`
  names it in the contract (a new revision with a ``DEPENDS_ON`` edge, kind
  ``contract_link``, to the item's head). Interface edges between the linked
  items are then derived on read (:mod:`twin_core.impact.interfaces`), so the
  impact walker follows a change across the contract.
* **Agreement.** :func:`agree` records one owning domain's agreement as a
  design decision pinned to the contract revision (``contract_agreement`` in
  its metadata). Only a person agrees: the actor comes from the request (the
  dashboard's click, or a verified principal), and an agent, a tool or a
  project policy is refused. A contract is ``agreed`` once every owning domain
  agreed to its current interface version.
* **Checks.** :func:`check_contracts` builds each contract's inputs from the
  linked items' current heads, the pin map's rows and the ``pin_maps_to``
  edges, the manufacturing context and the artifacts found, evaluates it
  (:func:`twin_core.contracts.checker.evaluate_contract`) and records the
  result as an ``interface_contract_check`` record pinned to every revision it
  read. A later head move of any of them marks the record stale through the
  existing record pins, and the impact report lists it.

Nothing here invents a value: a missing input is ``not_evaluated`` with the
step that would produce it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog

from observability.tracing import get_tracer
from twin_core.contracts.checker import (
    ArtifactSnapshot,
    CheckInputs,
    ItemSnapshot,
    agreed_domains,
    contract_status,
    evaluate_contract,
    outcome_words,
    validation_status_of,
)
from twin_core.contracts.records import (
    StoredContract,
    contract_from_metadata,
    list_contracts,
    read_agreements,
)
from twin_core.models.interface_contract import (
    CHECK_META_KEY,
    CHECK_PRODUCER,
    CONTRACT_CHECK_TYPE,
    CONTRACT_ITEM_TYPE,
    CONTRACT_KEYS,
    CONTRACT_META_KEY,
    INTERFACE_VERSION_KEY,
    CheckOutcome,
    ContractEvaluation,
    ContractKind,
    ContractStatus,
    InterfaceContract,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.interface_contracts")

#: ``DEPENDS_ON`` edge kind from a contract revision to a linked item's head.
LINK_EDGE_KIND = "contract_link"
#: Actor prefixes that are not a person: they may never agree to a contract.
NOT_A_PERSON_PREFIXES: tuple[str, ...] = ("metaforge", "agent", "tool", "policy", "system")

_metrics: Any = None
_locks: dict[tuple[UUID, str], asyncio.Lock] = {}


def _collector() -> Any:
    global _metrics  # noqa: PLW0603
    if _metrics is None:
        from observability.metrics import collector_for

        _metrics = collector_for("metaforge-twin-contracts")
    return _metrics


def _count_check(kind: str, outcome: str) -> None:
    try:
        _collector().record_interface_contract_check(kind, outcome)
    except Exception:  # noqa: BLE001 - metrics never break a check  # pragma: no cover
        pass


def _count_violation(kind: str, check: str) -> None:
    try:
        _collector().record_interface_contract_violation(kind, check)
    except Exception:  # noqa: BLE001  # pragma: no cover
        pass


class ContractError(ValueError):
    """A request that cannot be honoured. ``status`` is the HTTP status it maps to."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _pid(project_id: UUID | str) -> UUID:
    try:
        return project_id if isinstance(project_id, UUID) else UUID(str(project_id))
    except ValueError as exc:
        raise ContractError(f"invalid project id {project_id!r}") from exc


def _lock(pid: UUID, key: str) -> asyncio.Lock:
    lock = _locks.get((pid, key))
    if lock is None:
        lock = _locks[(pid, key)] = asyncio.Lock()
    return lock


def _now() -> str:
    return datetime.now(UTC).isoformat()


def interface_hash(contract: InterfaceContract) -> str:
    """What the interface version follows: values, owners and artifacts, not links."""
    body = contract.model_dump(
        mode="json", include={"kind", "owning_domains", "parameters", "artifacts"}
    )
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def is_person(actor: str) -> bool:
    """True for an actor that may agree: a person, not an agent, tool or policy."""
    text = (actor or "").strip().lower()
    return bool(text) and not text.startswith(NOT_A_PERSON_PREFIXES)


# ---------------------------------------------------------------------------
# Reading and writing contracts
# ---------------------------------------------------------------------------


async def get_contract(twin: Any, project_id: UUID | str, key: str) -> StoredContract | None:
    """The contract ``key`` at its head, or ``None``."""
    wanted = (key or "").strip().upper()
    for stored in await list_contracts(twin, _pid(project_id)):
        if stored.key == wanted:
            return stored
    return None


async def write_contract(
    twin: Any,
    project_id: UUID | str,
    contract: InterfaceContract,
    *,
    actor: str,
    source: str,
    change_reason: str = "",
    project_backend: Any = None,
) -> tuple[StoredContract, bool]:
    """Store ``contract`` as the next revision of its item when anything changed.

    Returns ``(stored, created)``. An identical contract is not written again.
    """
    from api_gateway.twin.item_revisions import finish_definition_revision
    from twin_core.items import find_item, plan_revision, supports_items
    from twin_core.models.enums import EdgeType, WorkProductType
    from twin_core.models.work_product import WorkProduct

    if twin is None or not supports_items(twin):
        raise ContractError("this twin cannot keep interface contracts", 503)
    pid = _pid(project_id)
    key = CONTRACT_KEYS[contract.kind]
    async with _lock(pid, key):
        with tracer.start_as_current_span("twin.interface_contracts.write") as span:
            span.set_attribute("project_id", str(pid))
            span.set_attribute("contract.key", key)
            existing = await get_contract(twin, pid, key)
            body = contract.model_dump(mode="json")
            if existing is not None and existing.contract.model_dump(mode="json") == body:
                return existing, False
            version = 1
            if existing is not None:
                version = existing.interface_version
                if interface_hash(existing.contract) != interface_hash(contract):
                    version += 1
            # The linked items' heads, resolved before anything is written.
            linked: list[tuple[str, int, UUID]] = []
            for link in contract.links:
                if not link.item_key:
                    continue
                item = await find_item(twin, link.item_key, pid)
                if item is None or item.head_node_id is None:
                    raise ContractError(f"no item {link.item_key} with a current revision", 404)
                if link.item_types and item.item_type not in link.item_types:
                    raise ContractError(
                        f"{item.key} is a {item.item_type}; the {link.role} side takes "
                        + ", ".join(link.item_types),
                        422,
                    )
                linked.append((item.key, item.head_revision, item.head_node_id))
            plan = await plan_revision(
                twin,
                item_type=CONTRACT_ITEM_TYPE,
                name=contract.title,
                project_id=pid,
                author=actor or source,
                item_key=key,
                change_reason=change_reason or f"interface contract from {source}",
                run_id=None,
            )
            if plan is None:
                raise ContractError("this twin cannot keep interface contracts", 503)
            content = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            metadata: dict[str, Any] = {
                CONTRACT_META_KEY: body,
                INTERFACE_VERSION_KEY: version,
                "interface_sha256": interface_hash(contract),
                "linked_items": [f"{k}@{r}" for k, r, _ in linked],
                "source": source,
                **plan.stamp(),
            }
            now = datetime.now(UTC)
            wp_id = uuid4()
            created = await twin.create_work_product(
                WorkProduct(
                    id=wp_id,
                    name=contract.title,
                    type=WorkProductType.INTERFACE_CONTRACT,
                    domain="systems",
                    file_path="",
                    content_hash=hashlib.sha256(content).hexdigest(),
                    format="json",
                    metadata=metadata,
                    created_at=now,
                    updated_at=now,
                    created_by=actor or source,
                    project_id=pid,
                )
            )
            node_id = getattr(created, "id", wp_id)
            result: dict[str, Any] = {}
            await finish_definition_revision(
                twin, plan, node_id, name=contract.title, result=result
            )
            for item_key, revision, head in linked:
                await twin.add_edge(
                    node_id,
                    head,
                    EdgeType.DEPENDS_ON,
                    metadata={
                        "kind": LINK_EDGE_KIND,
                        "item_key": item_key,
                        "revision": revision,
                        "item_ref": f"{item_key}@{revision}",
                    },
                )
            if project_backend is not None:
                try:
                    await project_backend.link_work_product(
                        str(pid), str(node_id), contract.title, CONTRACT_ITEM_TYPE
                    )
                except Exception as exc:  # noqa: BLE001 - the link is best-effort
                    logger.warning("interface_contract_project_link_failed", error=str(exc))
            stored = StoredContract(
                key=key,
                revision=int(result.get("revision") or plan.revision),
                node_id=str(node_id),
                contract=contract,
                metadata=metadata,
            )
            span.set_attribute("contract.ref", stored.ref)
            logger.info(
                "interface_contract_written",
                project_id=str(pid),
                contract_ref=stored.ref,
                interface_version=version,
                linked=metadata["linked_items"],
                source=source,
            )
            return stored, True


async def link_item(
    twin: Any,
    project_id: UUID | str,
    key: str,
    *,
    role: str,
    item_key: str | None,
    actor: str,
    project_backend: Any = None,
) -> StoredContract:
    """Name the item that implements ``role`` (``None`` unlinks it). A new revision."""
    stored = await get_contract(twin, project_id, key)
    if stored is None:
        raise ContractError(f"no interface contract {key} in this project", 404)
    link = stored.contract.link(role)
    if link is None:
        roles = ", ".join(lk.role for lk in stored.contract.links)
        raise ContractError(f"{stored.key} has no side {role!r}; its sides are {roles}", 422)
    links = [
        lk.model_copy(update={"item_key": (item_key or "").strip().upper() or None})
        if lk.role == role
        else lk
        for lk in stored.contract.links
    ]
    updated = stored.contract.model_copy(update={"links": links})
    out, _ = await write_contract(
        twin,
        project_id,
        updated,
        actor=actor,
        source="link",
        change_reason=f"{role} linked to {item_key}" if item_key else f"{role} unlinked",
        project_backend=project_backend,
    )
    return out


async def agree(
    twin: Any,
    project_id: UUID | str,
    key: str,
    *,
    domain: str,
    actor: str,
    rationale: str = "",
    project_backend: Any = None,
) -> dict[str, Any]:
    """One owning domain agrees to the contract's current interface version.

    Recorded as a design decision pinned to the contract revision. Refused for
    an actor that is not a person. Agreeing twice is a no-op.
    """
    from api_gateway.twin.decision_recorder import make_decision_recorder

    pid = _pid(project_id)
    with tracer.start_as_current_span("twin.interface_contracts.agree") as span:
        span.set_attribute("project_id", str(pid))
        span.set_attribute("contract.key", key)
        if not is_person(actor):
            raise ContractError(
                "agreeing to an interface contract is a person's decision; "
                f"{actor or 'nobody'} cannot agree",
                403,
            )
        stored = await get_contract(twin, pid, key)
        if stored is None:
            raise ContractError(f"no interface contract {key} in this project", 404)
        wanted = (domain or "").strip().lower()
        if wanted not in stored.contract.owning_domains:
            raise ContractError(
                f"{wanted or 'that domain'} does not own {stored.key}; its owners are "
                + ", ".join(stored.contract.owning_domains),
                422,
            )
        version = stored.interface_version
        mine = (await read_agreements(twin, pid)).get(stored.key, [])
        if wanted in agreed_domains(mine, version):
            existing = next(
                a for a in mine if a.domain == wanted and a.interface_version == version
            )
            return {"decision_id": existing.decision_id, "created": False, "contract": stored.ref}
        recorder = make_decision_recorder(twin, project_backend)
        at = _now()
        result = await recorder(
            title=f"Agree {stored.contract.title} v{version} for {wanted}",
            rationale=rationale
            or f"{wanted} agrees to build against {stored.key} interface version {version}.",
            project_id=str(pid),
            domain=wanted,
            depends_on=[stored.ref],
            contract_agreement={
                "contract_key": stored.key,
                "contract_revision": stored.revision,
                "interface_version": version,
                "domain": wanted,
                "decided_by": actor,
                "at": at,
                "rationale": rationale,
            },
        )
        logger.info(
            "interface_contract_agreed",
            project_id=str(pid),
            contract_ref=stored.ref,
            interface_version=version,
            domain=wanted,
            decided_by=actor,
            decision_id=result.get("node_id"),
        )
        return {"decision_id": result.get("node_id"), "created": True, "contract": stored.ref}


# ---------------------------------------------------------------------------
# Inputs for the checks
# ---------------------------------------------------------------------------


def _files_of(meta: dict[str, Any]) -> list[str]:
    names: list[str] = []
    design = meta.get("design_files")
    if isinstance(design, dict):
        names += [str(k) for k in design]
    for key in ("original_filename", "file_name"):
        if meta.get(key):
            names.append(str(meta[key]))
    files = meta.get("files")
    if isinstance(files, list):
        for f in files:
            names.append(
                str(f.get("name") or f.get("path") or "") if isinstance(f, dict) else str(f)
            )
    return [n for n in names if n]


async def _source_pins(twin: Any, node: Any, heads: dict[str, int]) -> list[dict[str, str]]:
    """``{pinned, current}`` per source revision an artifact records."""
    from twin_core.consistency.record_pins import record_pins
    from twin_core.models.enums import EdgeType

    meta = dict(getattr(node, "metadata", None) or {})
    found: dict[str, tuple[str, int]] = {}
    for pin in record_pins(meta):
        found[str(pin["item_key"])] = (str(pin["item_key"]), int(pin.get("revision") or 0))
    try:
        edges = await twin.graph.get_edges(
            node.id, direction="outgoing", edge_type=EdgeType.DEPENDS_ON
        )
    except Exception:  # noqa: BLE001 - no edges read as none recorded
        edges = []
    for edge in edges:
        em = edge.metadata or {}
        if em.get("item_key") and em.get("revision"):
            found.setdefault(str(em["item_key"]), (str(em["item_key"]), int(em["revision"])))
    out = []
    for item_key, revision in found.values():
        current = heads.get(item_key)
        out.append(
            {
                "pinned": f"{item_key}@{revision}",
                "current": f"{item_key}@{current}" if current else "",
            }
        )
    return out


async def build_inputs(twin: Any, project_id: UUID | str, stored: StoredContract) -> CheckInputs:
    """What ``stored``'s checks read, from the twin as it is now."""
    from api_gateway.twin.interface_proposals import parse_pinmap, recorded_files
    from twin_core.consistency.record_pins import record_pins
    from twin_core.consistency.validation_status import verdict_of_result
    from twin_core.impact.interfaces import list_stored
    from twin_core.items import list_items
    from twin_core.models.interface import InterfaceType

    pid = _pid(project_id)
    contract = stored.contract
    all_items = {i.key: i for i in await list_items(twin, project_id=pid)}
    heads = {k: i.head_revision for k, i in all_items.items() if i.head_node_id is not None}
    inputs = CheckInputs()
    nodes: dict[str, Any] = {}
    for link in contract.links:
        item = all_items.get(link.item_key or "")
        if item is None or item.head_node_id is None:
            continue
        node = await twin.graph.get_node(item.head_node_id)
        nodes[link.role] = node
        inputs.items[link.role] = ItemSnapshot(
            key=item.key,
            revision=item.head_revision,
            item_type=item.item_type,
            name=item.name,
            metadata=dict(getattr(node, "metadata", None) or {}),
        )

    if contract.kind is ContractKind.EDA_FIRMWARE and "pinmap" in inputs.items:
        rows = [
            {"pin": e.pin, "signal": e.signal, "net": e.net, "peripheral": e.peripheral}
            for e in parse_pinmap(recorded_files(nodes["pinmap"]))
        ]
        if not rows:
            raw = inputs.items["pinmap"].metadata.get("pins")
            if isinstance(raw, list):
                rows = [
                    {k: str(r.get(k) or "") for k in ("pin", "signal", "net", "peripheral")}
                    for r in raw
                    if isinstance(r, dict)
                ]
        inputs.pinmap = rows
        firmware = inputs.items.get("firmware")
        if firmware is not None:
            pinmap_key = inputs.items["pinmap"].key
            for edge in await list_stored(twin, pid):
                if edge.type is not InterfaceType.PIN_MAPS_TO:
                    continue
                if {edge.source.ref, edge.target.ref} != {pinmap_key, firmware.key}:
                    continue
                inputs.pin_edge = True
                names = set(edge.coupled_parameters)
                matched = edge.evidence.get("matched") if edge.evidence else None
                if isinstance(matched, dict):
                    names |= {str(k) for k in matched}
                inputs.firmware_pins = sorted(set(inputs.firmware_pins) | names)

    if contract.kind is ContractKind.SIMULATION:
        from api_gateway.twin.manufacturing_context import effective_context
        from twin_core.manufacturing.context import CONTEXT_FIELDS

        try:
            context = await effective_context(twin, pid, None)
        except Exception as exc:  # noqa: BLE001 - an unreadable context is a missing input
            logger.warning("interface_contract_context_unreadable", error=str(exc))
            context = None
        if context is not None:
            inputs.context_ref = context.ref
            for name in CONTEXT_FIELDS:
                field = context.record.get(name)
                inputs.context[name] = {"value": field.value, "authority": field.authority.value}

    if contract.kind is ContractKind.MANUFACTURING:
        for artifact in contract.artifacts:
            found: list[ArtifactSnapshot] = []
            for item_type in artifact.item_types:
                for item in sorted(
                    (i for i in all_items.values() if i.item_type == item_type),
                    key=lambda i: i.updated_at,
                ):
                    if item.head_node_id is None:
                        continue
                    node = await twin.graph.get_node(item.head_node_id)
                    meta = dict(getattr(node, "metadata", None) or {})
                    found.append(
                        ArtifactSnapshot(
                            ref=f"{item.key}@{item.head_revision}",
                            item_type=item.item_type,
                            component_id=meta.get("component_id"),
                            files=_files_of(meta),
                            pins=await _source_pins(twin, node, heads),
                        )
                    )
            if artifact.producer_tool:
                from twin_core.impact.cross_domain import producer_tool

                for entity in await twin.list_engineering_entities(
                    project_id=pid, entity_type="evidence"
                ):
                    meta = dict(entity.metadata or {})
                    if producer_tool(meta) != artifact.producer_tool:
                        continue
                    result = meta.get("result") if isinstance(meta.get("result"), dict) else {}
                    found.append(
                        ArtifactSnapshot(
                            ref=f"record:{entity.id}",
                            item_type="evidence",
                            pins=[
                                {
                                    "pinned": str(p.get("item_ref")),
                                    "current": f"{p['item_key']}@{heads[p['item_key']]}"
                                    if p.get("item_key") in heads
                                    else "",
                                }
                                for p in record_pins(meta)
                            ],
                            status=verdict_of_result(result).value,
                        )
                    )
            inputs.artifacts[artifact.kind] = found
    return inputs


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


async def _record_check(
    twin: Any, pid: UUID, stored: StoredContract, evaluation: ContractEvaluation, actor: str
) -> str:
    from twin_core.consistency.record_pins import (
        link_record,
        pin_metadata,
        record_staleness,
        resolve_pins,
        set_record_status,
    )
    from twin_core.consistency.staleness import StalenessStatus
    from twin_core.models.enums import WorkProductType
    from twin_core.models.work_product import WorkProduct

    refs = [r for r in evaluation.inputs if "@" in r and not r.startswith("record:")]
    pins = await resolve_pins(twin, refs=refs, project_id=pid)
    status = validation_status_of(evaluation)
    body = evaluation.model_dump(mode="json")
    name = f"Contract check: {stored.key}"
    metadata: dict[str, Any] = {
        CHECK_META_KEY: body,
        "producer": {"tool": CHECK_PRODUCER},
        "result": {"status": status.value, "outcome": evaluation.outcome.value},
        "contract_key": stored.key,
        **pin_metadata(pins),
    }
    content = json.dumps(body, sort_keys=True).encode()
    wp = WorkProduct(
        id=uuid4(),
        name=name,
        type=WorkProductType.INTERFACE_CONTRACT_CHECK,
        domain="systems",
        file_path="",
        content_hash=hashlib.sha256(content).hexdigest(),
        format="json",
        metadata=metadata,
        created_by=actor or CHECK_PRODUCER,
        project_id=pid,
    )
    prior = await twin.list_work_products(
        work_product_type=WorkProductType.INTERFACE_CONTRACT_CHECK, project_id=pid
    )
    created = await twin.create_work_product(wp)
    record_id = getattr(created, "id", wp.id)
    await link_record(
        twin, record_id, pins, record_type=CONTRACT_CHECK_TYPE, name=name, metadata=metadata
    )
    # A re-check replaces the contract's earlier checks; they stay as history.
    for node in prior:
        meta = dict(node.metadata or {})
        if meta.get("contract_key") != stored.key:
            continue
        if record_staleness(meta) in ("superseded", "invalid"):
            continue
        await set_record_status(
            twin, node.id, StalenessStatus.SUPERSEDED, reason=f"re-checked as {record_id}"
        )
    return str(record_id)


async def check_contract(
    twin: Any, project_id: UUID | str, stored: StoredContract, *, actor: str = ""
) -> tuple[ContractEvaluation, str]:
    """Evaluate one contract and record the result. Returns ``(evaluation, record id)``."""
    pid = _pid(project_id)
    kind = stored.contract.kind.value
    with tracer.start_as_current_span("twin.interface_contracts.check") as span:
        span.set_attribute("project_id", str(pid))
        span.set_attribute("contract.ref", stored.ref)
        try:
            inputs = await build_inputs(twin, pid, stored)
            evaluation = evaluate_contract(
                stored.contract,
                contract_key=stored.key,
                contract_revision=stored.revision,
                interface_version=stored.interface_version,
                inputs=inputs,
            )
            record_id = await _record_check(twin, pid, stored, evaluation, actor)
        except Exception as exc:
            span.record_exception(exc)
            _count_check(kind, "failed")
            logger.warning(
                "interface_contract_check_failed",
                project_id=str(pid),
                contract_ref=stored.ref,
                error=str(exc),
            )
            raise
        span.set_attribute("contract.outcome", evaluation.outcome.value)
        _count_check(kind, evaluation.outcome.value)
        for check in evaluation.checks:
            if check.outcome is CheckOutcome.VIOLATED:
                _count_violation(kind, check.id)
        logger.info(
            "interface_contract_checked",
            project_id=str(pid),
            contract_ref=stored.ref,
            outcome=evaluation.outcome.value,
            counts=evaluation.counts(),
            record_id=record_id,
            inputs=evaluation.inputs,
        )
        return evaluation, record_id


async def check_contracts(
    twin: Any, project_id: UUID | str, *, key: str | None = None, actor: str = ""
) -> list[tuple[ContractEvaluation, str]]:
    """Evaluate every contract (or ``key``) against the current revisions."""
    pid = _pid(project_id)
    contracts = await list_contracts(twin, pid)
    if key:
        contracts = [c for c in contracts if c.key == key.strip().upper()]
        if not contracts:
            raise ContractError(f"no interface contract {key} in this project", 404)
    return [await check_contract(twin, pid, stored, actor=actor) for stored in contracts]


async def latest_checks(twin: Any, project_id: UUID | str) -> dict[str, dict[str, Any]]:
    """The newest recorded check per contract key, with its staleness."""
    from twin_core.consistency.record_pins import record_staleness
    from twin_core.consistency.validation_status import check_result_for_record
    from twin_core.models.enums import WorkProductType

    pid = _pid(project_id)
    nodes = await twin.list_work_products(
        work_product_type=WorkProductType.INTERFACE_CONTRACT_CHECK, project_id=pid
    )
    out: dict[str, dict[str, Any]] = {}
    # Superseded checks first, so the one a re-check left current wins a tie.
    ordered = sorted(
        nodes,
        key=lambda n: (
            record_staleness(n.metadata) != "superseded",
            n.created_at,
            str(n.id),
        ),
    )
    for node in ordered:
        meta = dict(node.metadata or {})
        raw = meta.get(CHECK_META_KEY)
        if not isinstance(raw, dict):
            continue
        try:
            evaluation = ContractEvaluation.model_validate(raw)
        except Exception:  # noqa: BLE001 - an unreadable check is skipped, not fatal
            continue
        staleness = record_staleness(meta)
        if staleness == "superseded" and evaluation.contract_key in out:
            continue  # a re-check replaced it
        result = check_result_for_record(str(node.id), meta, check="contract", tool=CHECK_PRODUCER)
        out[evaluation.contract_key] = {
            "record_id": str(node.id),
            "evaluation": evaluation,
            "staleness": staleness,
            "stale": staleness not in ("current", "revalidated"),
            "staleness_reason": meta.get("staleness_reason"),
            "validation": result.model_dump(mode="json"),
        }
    return out


# ---------------------------------------------------------------------------
# The view
# ---------------------------------------------------------------------------


def _create_line(
    stored: StoredContract, status: ContractStatus, latest: dict[str, Any] | None
) -> str:
    if latest is None:
        words = outcome_words(None)
    elif latest["stale"] or status is ContractStatus.STALE:
        words = outcome_words(CheckOutcome.STALE)
    else:
        words = outcome_words(latest["evaluation"].outcome)
    return f"{stored.contract.plain}: {words}"


async def contracts_view(twin: Any, project_id: UUID | str) -> dict[str, Any]:
    """Every contract with its links, agreements, latest check and status."""
    from twin_core.items import list_items

    pid = _pid(project_id)
    with tracer.start_as_current_span("twin.interface_contracts.view") as span:
        span.set_attribute("project_id", str(pid))
        contracts = await list_contracts(twin, pid)
        agreements = await read_agreements(twin, pid)
        checks = await latest_checks(twin, pid)
        heads = {
            i.key: i.head_revision
            for i in await list_items(twin, project_id=pid)
            if i.head_node_id is not None
        }
        out = []
        for stored in contracts:
            version = stored.interface_version
            mine = [a for a in agreements.get(stored.key, []) if a.interface_version == version]
            latest = checks.get(stored.key)
            status = contract_status(
                stored.contract,
                contract_revision=stored.revision,
                interface_version=version,
                agreements=mine,
                latest=latest["evaluation"] if latest else None,
                latest_is_stale=bool(latest and latest["stale"]),
            )
            done = agreed_domains(mine, version)
            links = []
            for link in stored.contract.links:
                head = heads.get(link.item_key or "")
                links.append(
                    {
                        **link.model_dump(mode="json"),
                        "linked": head is not None,
                        "head_ref": f"{link.item_key}@{head}" if head else None,
                    }
                )
            out.append(
                {
                    "key": stored.key,
                    "ref": stored.ref,
                    "revision": stored.revision,
                    "interface_version": version,
                    "kind": stored.contract.kind.value,
                    "title": stored.contract.title,
                    "plain": stored.contract.plain,
                    "owning_domains": list(stored.contract.owning_domains),
                    "parameters": [p.model_dump(mode="json") for p in stored.contract.parameters],
                    "links": links,
                    "artifacts": [a.model_dump(mode="json") for a in stored.contract.artifacts],
                    "rationale": stored.contract.rationale,
                    "agreements": [a.model_dump(mode="json") for a in mine],
                    "awaiting_agreement": [
                        d for d in stored.contract.owning_domains if d not in done
                    ],
                    "status": status.value,
                    "create_line": _create_line(stored, status, latest),
                    "latest_check": (
                        {
                            "record_id": latest["record_id"],
                            "staleness": latest["staleness"],
                            "stale": latest["stale"],
                            "staleness_reason": latest["staleness_reason"],
                            "validation": latest["validation"],
                            **latest["evaluation"].model_dump(mode="json"),
                        }
                        if latest
                        else None
                    ),
                }
            )
        span.set_attribute("contracts.count", len(out))
        return {"project_id": str(pid), "contracts": out}


def contract_from_body(raw: Any) -> InterfaceContract:
    """A contract from a request or a definition file; ``ContractError`` when invalid."""
    found = contract_from_metadata({CONTRACT_META_KEY: raw})
    if found is None:
        try:
            InterfaceContract.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - say why
            raise ContractError(f"invalid interface contract: {exc}", 422) from exc
        raise ContractError("invalid interface contract", 422)
    return found
