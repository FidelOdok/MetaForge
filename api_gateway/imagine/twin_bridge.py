"""How the Imagine studio reaches the twin, through the twin's own services.

Each method writes through the recorder the rest of MetaForge uses for that
kind of record, so a studio choice is an ordinary design decision, a studio
requirement an ordinary constraint, and so on:

| Studio | Twin service |
|---|---|
| Requirements, scenarios, custom requirements | ``constraint_recorder`` (``CS-IMAGINE``) |
| Intent ledger statements | ``engineering_entity_recorder`` (``intent``, ``INT-IMG-L<n>``) |
| Assumptions | ``engineering_entity_recorder`` (``assumption``) and the MFC |
| Option choices | ``decision_recorder`` |
| Parts (at handover) | ``decision_recorder`` with ``assembly``: CMP ids |
| Context edits | ``manufacturing_context.write_context`` |
| Interfaces (at handover) | ``interface_contracts.write_contract`` (proposed) |
| Screening results | ``engineering_entity_recorder`` (``evidence``, V2) |

Nothing written here claims more than the browser can know: decisions,
constraints and entities are proposals at their default authority, MFC
fields are assumed or stated (never confirmed by a decision), contract
values are estimated, and screening is preliminary guidance at V2.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.imagine.twin_bridge")

SOURCE = "imagine.studio"
REQUIREMENTS_KEY = "CS-IMAGINE"
REQUIREMENTS_TITLE = "Imagine requirements"

#: formgraph's words for a manufacturing context field.
MFC_FIELD_ALIASES: dict[str, str] = {
    "process": "selected_process",
    "material": "selected_material",
    "qty": "quantity",
    "quantity": "quantity",
    "volume": "quantity",
    "finish": "finish",
    "fixings": "fastening",
    "fastening": "fastening",
    "machine": "machine_or_supplier_profile",
    "supplier": "machine_or_supplier_profile",
    "size": "dimensional_envelope",
    "envelope": "dimensional_envelope",
    "use": "intended_use",
}


def ref_of(result: dict[str, Any], kind: str) -> str:
    """A stable reference for a recorder's result: ``KEY@n`` for a definition, else the node."""
    item_ref = result.get("item_ref")
    if item_ref:
        return str(item_ref)
    return f"{kind}:{result.get('node_id')}"


def mfc_field(name: str) -> str | None:
    """The MFC field a formgraph name refers to, or ``None``."""
    from twin_core.manufacturing.context import CONTEXT_FIELDS

    word = (name or "").strip().lower()
    if word in CONTEXT_FIELDS:
        return word
    head = word.split(" ")[0].split(".")[-1]
    return MFC_FIELD_ALIASES.get(word) or MFC_FIELD_ALIASES.get(head)


class TwinBridge:
    """The twin writes the Imagine service needs, bound to one twin."""

    def __init__(self, twin: Any, project_backend: Any = None) -> None:
        from api_gateway.twin.constraint_recorder import make_constraint_recorder
        from api_gateway.twin.decision_recorder import make_decision_recorder
        from api_gateway.twin.engineering_entity_recorder import (
            make_engineering_entity_recorder,
        )

        self.twin = twin
        self.project_backend = project_backend
        self._constraints = make_constraint_recorder(twin, project_backend)
        self._decisions = make_decision_recorder(twin, project_backend)
        self._entities = make_engineering_entity_recorder(twin, project_backend)

    async def record_requirements(
        self, project_id: str, requirements: list[dict[str, Any]], reason: str
    ) -> str | None:
        """Re-record every studio requirement as the next revision of ``CS-IMAGINE``."""
        if not requirements:
            return None
        entries = []
        for r in requirements[:200]:
            source = str(r.get("source") or "").strip()
            entries.append(
                {
                    "name": r["name"],
                    "expression": "True",
                    "severity": "error" if r.get("priority") == "Must" else "warning",
                    "message": str(r.get("text") or "")[:2000],
                    "domain": "systems",
                    "acceptance_criteria": str(r.get("acceptance") or "")[:1000],
                    "verification_method": str(r.get("verify") or ""),
                    "limit_source": {"kind": "user", "statement": source}
                    if source
                    else {"kind": "assumption", "rationale": "inferred in the Imagine studio"},
                }
            )
        with tracer.start_as_current_span("imagine.twin.requirements") as span:
            span.set_attribute("requirements.count", len(entries))
            result = await self._constraints(
                title=REQUIREMENTS_TITLE,
                constraints=entries,
                project_id=project_id,
                item_key=REQUIREMENTS_KEY,
                change_reason=reason,
            )
        return ref_of(result, "constraint_set")

    async def record_decision(
        self,
        project_id: str,
        *,
        title: str,
        rationale: str,
        alternatives: list[str] | None = None,
        depends_on: list[str] | None = None,
        assembly: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        with tracer.start_as_current_span("imagine.twin.decision"):
            result = await self._decisions(
                title=title[:200],
                rationale=rationale,
                alternatives=[
                    {"option": a, "reason_rejected": "not chosen in the Imagine studio"}
                    for a in (alternatives or [])
                ],
                project_id=project_id,
                depends_on=depends_on or None,
                assembly=assembly,
            )
        return f"decision:{result.get('node_id')}", result

    async def record_entity(
        self,
        project_id: str,
        *,
        entity_type: str,
        statement: str,
        title: str | None = None,
        extra: dict[str, Any] | None = None,
        item_key: str | None = None,
        reason: str | None = None,
    ) -> str:
        with tracer.start_as_current_span("imagine.twin.entity") as span:
            span.set_attribute("entity.type", entity_type)
            result = await self._entities(
                entity_type=entity_type,
                statement=statement[:4000],
                title=(title or statement)[:120],
                extra={**(extra or {}), "recorded_from": SOURCE},
                project_id=project_id,
                item_key=item_key,
                change_reason=reason,
            )
        return ref_of(result, entity_type)

    async def write_context(
        self,
        project_id: str,
        fields: dict[str, tuple[Any, str]],
        *,
        actor: str,
        ref: str | None,
        reason: str,
    ) -> str | None:
        """Write MFC fields, each ``(value, "assumed"|"stated")``, with provenance."""
        from api_gateway.twin import manufacturing_context as mc
        from twin_core.manufacturing.context import (
            NEVER_ASSUMED,
            Authority,
            ContextField,
            Provenance,
        )

        updates: dict[str, Any] = {}
        for name, (value, how) in fields.items():
            if value in (None, "", [], {}):
                continue
            if how == "assumed" and name in NEVER_ASSUMED:
                continue
            authority = Authority.USER_STATED if how == "stated" else Authority.ASSUMPTION
            updates[name] = ContextField(
                value=value,
                authority=authority,
                provenance=Provenance(source=SOURCE, actor=actor, ref=ref),
                note="From the Imagine studio (screening, V2 at most).",
            )
        if not updates:
            return None
        with tracer.start_as_current_span("imagine.twin.context") as span:
            span.set_attribute("context.fields", len(updates))
            outcome = await mc.write_context(
                self.twin,
                project_id,
                updates,
                source=SOURCE,
                actor=actor,
                change_reason=reason,
                project_backend=self.project_backend,
            )
        stored = outcome.stored
        return stored.ref if stored is not None else None

    async def write_contract(self, project_id: str, contract: Any, *, actor: str) -> str:
        from api_gateway.twin.interface_contracts import write_contract

        with tracer.start_as_current_span("imagine.twin.contract"):
            stored, _created = await write_contract(
                self.twin,
                project_id,
                contract,
                actor=actor,
                source=SOURCE,
                change_reason="proposed in the Imagine studio",
                project_backend=self.project_backend,
            )
        return str(stored.ref)

    async def has_concept_mesh(self, project_id: str) -> bool:
        """An illustrative concept mesh (GLB) is recorded for the project."""
        try:
            wps = await self.twin.list_work_products(project_id=UUID(project_id))
        except Exception as exc:  # noqa: BLE001 - a read failure reads as missing
            logger.warning("imagine_concept_mesh_lookup_failed", error=str(exc))
            return False
        for wp in wps:
            meta = getattr(wp, "metadata", None) or {}
            if getattr(wp, "format", "") == "glb" and meta.get("authority") == "illustrative":
                return True
        return False
