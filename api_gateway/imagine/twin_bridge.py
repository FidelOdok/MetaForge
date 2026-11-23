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


#: A GLB of either authority shows the concept for the handover's concept assembly.
CONCEPT_MESH_AUTHORITIES = ("illustrative", "structured")


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
        from api_gateway.twin.component_recorder import make_component_recorder

        self._components = make_component_recorder(twin, project_backend)

    async def record_requirements(
        self, project_id: str, requirements: list[dict[str, Any]], reason: str
    ) -> str | None:
        """Re-record every studio requirement as the next revision of ``CS-IMAGINE``."""
        if not requirements:
            return None
        entries = []
        for r in requirements[:200]:
            source = str(r.get("source") or "").strip()
            # FORGE-903: a requirement with a number (the operating environment) is typed,
            # so a part's specs can be held against it; the rest stay statements.
            typed = (
                {k: r[k] for k in ("metric", "operator", "limit", "unit", "target_node_type")}
                if r.get("metric") and r.get("limit") is not None
                else {"expression": "True"}
            )
            entries.append(
                {
                    "name": r["name"],
                    **typed,
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

    async def record_candidates(
        self, project_id: str, chosen: list[tuple[str, dict[str, Any]]], reason: str
    ) -> list[str]:
        """Seed Develop with the studio's chosen bought parts, as candidates (FORGE-903).

        One candidate selection per order code, linked to every studio part it fills
        (two channels, one driver), so Develop starts from the concept's parts instead of
        typing them again. A part with no real order code or maker is left out, and so is
        a part Develop already has a selection for: a handover after a rework never puts
        a candidate over what Develop chose. Returns ``bom_item:<id>`` refs. Best effort:
        a part that cannot be recorded is logged and skipped.
        """
        from twin_core.component_thread import mpn_key, same_family

        existing: list[Any] = []
        try:
            existing = list(await self.twin.list_bom_items(UUID(project_id)))
        except Exception as exc:  # noqa: BLE001 - no list reads as none
            logger.warning("imagine_candidates_list_failed", error=str(exc))
        taken: set[str] = set()
        for item in existing:
            specs = dict(getattr(item, "specifications", None) or {})
            thread = specs.get("thread") if isinstance(specs.get("thread"), dict) else {}
            taken.update(str(p) for p in (thread or {}).get("studio_parts") or [])
        groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for role, part in chosen:
            pid = str(part.get("id") or "")
            maker = str(part.get("manufacturer") or "").strip()
            key = mpn_key(part.get("mpn"))
            if not pid or not key or not maker or "<" in maker or pid in taken:
                continue
            if any(same_family(getattr(i, "part_number", ""), part.get("mpn")) for i in existing):
                continue
            groups.setdefault(key, []).append((role, part))
        refs: list[str] = []
        for parts in groups.values():
            role, first = parts[0]
            try:
                out = await self._components(
                    mpn=str(first["mpn"]).strip(),
                    manufacturer=str(first["manufacturer"]).strip(),
                    category=role.split(":", 1)[-1] or "concept_part",
                    purchase_unit="discrete_part",
                    role=role,
                    quantity=len(parts),
                    datasheet_url=str(first.get("datasheet") or "") or None,
                    source=reason,
                    project_id=project_id,
                    selection="candidate",
                    studio_parts=[str(p.get("id")) for _, p in parts],
                )
                refs.append(f"bom_item:{out['node_id']}")
            except Exception as exc:  # noqa: BLE001 - one part never stops a handover
                logger.warning("imagine_candidate_failed", mpn=first.get("mpn"), error=str(exc))
        return refs

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
        """A concept mesh (GLB) is recorded for the project.

        Illustrative (a provider's look, ``twin.generate_concept_mesh``) or
        structured (one node per real part at its read size, built free from the
        layout by ``twin.render_structured_mesh``). Either shows the concept; a
        paid provider should not decide whether a design can be handed over.
        """
        try:
            wps = await self.twin.list_work_products(project_id=UUID(project_id))
        except Exception as exc:  # noqa: BLE001 - a read failure reads as missing
            logger.warning("imagine_concept_mesh_lookup_failed", error=str(exc))
            return False
        for wp in wps:
            meta = getattr(wp, "metadata", None) or {}
            glb = getattr(wp, "format", "") == "glb"
            if glb and meta.get("authority") in CONCEPT_MESH_AUTHORITIES:
                return True
        return False
