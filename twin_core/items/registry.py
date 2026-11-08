"""Twin type registry: versioned definitions vs append-only records (FORGE-522).

Every twin type falls into one of three kinds, and the write paths consult
this registry to decide what a write means:

- **definition**: a thing that is *revised*. It has an :class:`Item` with a
  stable key and exactly one current head. Re-recording it creates revision
  ``n+1`` of the same item, never a free-standing sibling node.
- **record**: a thing that *happened*. Append-only, never revised, never
  given an item. A second design decision is a second decision, not "the
  decision, revision 2".
- **derived**: a generated view of other nodes (``prd_view``). Not a source
  of truth; it gets neither an item nor record semantics.

FORGE-528: the ``prd`` work product is the prd's *prose* (background, scope,
non-requirements), a definition like any other. The prd a reader sees is the
derived ``prd_view``: that prose plus intent, needs, objectives and the
current constraint set, rendered on read.

A type absent from this table keeps its pre-FORGE-522 behaviour (one node per
write, no item) until it is classified. Classifying it here is the only step
needed to bring it under item identity, provided its write path calls
:mod:`twin_core.items.service`.

The table is mirrored in ``docs/twin_schema.md`` ("Definitions vs records");
``tests/unit/test_item_registry.py`` keeps the two in step.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TwinTypeKind(StrEnum):
    """How a twin type behaves on write."""

    DEFINITION = "definition"
    RECORD = "record"
    DERIVED = "derived"


@dataclass(frozen=True)
class TwinTypeSpec:
    """One registry row."""

    #: Registry name. For a definition this is also ``Item.item_type``.
    name: str
    kind: TwinTypeKind
    #: Where its nodes live: ``work_product:<WorkProductType>``,
    #: ``engineering_entity:<entity_type>``, ``bom_item``, or a non-twin store.
    storage: str
    #: Item key prefix (definitions only), e.g. ``CAD`` -> ``CAD-BRACKET``.
    key_prefix: str | None = None
    #: Definitions whose items may continue one another. A part that gains
    #: parts becomes an assembly without losing its history.
    family: str | None = None
    description: str = ""


_ROWS: tuple[TwinTypeSpec, ...] = (
    # -- definitions -------------------------------------------------------
    TwinTypeSpec(
        "cad_model",
        TwinTypeKind.DEFINITION,
        "work_product:cad_model",
        key_prefix="CAD",
        family="geometry",
        description="A part's geometry (twin.commit_geometry without parts).",
    ),
    TwinTypeSpec(
        "assembly",
        TwinTypeKind.DEFINITION,
        "work_product:cad_model",
        key_prefix="ASM",
        family="geometry",
        description="An assembly's geometry (twin.commit_geometry with parts).",
    ),
    TwinTypeSpec(
        "constraint_set",
        TwinTypeKind.DEFINITION,
        "work_product:constraint_set",
        key_prefix="CS",
        description="A requirement/constraint set (twin.record_constraint_set).",
    ),
    TwinTypeSpec(
        "intent",
        TwinTypeKind.DEFINITION,
        "engineering_entity:intent",
        key_prefix="INT",
        description="The engineering intent statement.",
    ),
    TwinTypeSpec(
        "stakeholder_need",
        TwinTypeKind.DEFINITION,
        "engineering_entity:stakeholder_need",
        key_prefix="NEED",
        description="A stakeholder need.",
    ),
    TwinTypeSpec(
        "design_value",
        TwinTypeKind.DEFINITION,
        "engineering_entity:design_value",
        key_prefix="VAL",
        description=(
            "One engineering value and its source (FORGE-586); a changed value "
            "or a better source is a new revision."
        ),
    ),
    TwinTypeSpec(
        "objective",
        TwinTypeKind.DEFINITION,
        "engineering_entity:objective",
        key_prefix="OBJ",
        description="An optimisation objective (metric, direction, target).",
    ),
    TwinTypeSpec(
        "bom",
        TwinTypeKind.DEFINITION,
        "work_product:bom",
        key_prefix="BOM",
        description="A bill of materials (electronics BOM recorder).",
    ),
    TwinTypeSpec(
        "component_selection",
        TwinTypeKind.DEFINITION,
        "bom_item",
        key_prefix="CMP",
        description=(
            "The part chosen for one role (twin.record_component_selection); "
            "choosing a different part for the same role is a new revision."
        ),
    ),
    TwinTypeSpec(
        "prd",
        TwinTypeKind.DEFINITION,
        "work_product:prd",
        key_prefix="PRD",
        description=(
            "The prd prose: background, scope, non-requirements "
            "(twin.record_document with document_type='prd'). Requirement "
            "values live in the constraint set."
        ),
    ),
    # FORGE-687: the electronics and firmware design files, recorded by
    # twin.record_design_files from what the KiCad and firmware adapters wrote.
    TwinTypeSpec(
        "schematic",
        TwinTypeKind.DEFINITION,
        "work_product:schematic",
        key_prefix="SCH",
        description="A KiCad schematic, every sheet of it (twin.record_design_files).",
    ),
    TwinTypeSpec(
        "pcb_layout",
        TwinTypeKind.DEFINITION,
        "work_product:pcb_layout",
        key_prefix="PCB",
        description="A KiCad board (twin.record_design_files).",
    ),
    TwinTypeSpec(
        "gerber",
        TwinTypeKind.DEFINITION,
        "work_product:gerber",
        key_prefix="GBR",
        description="A board's gerber and drill set (twin.record_design_files).",
    ),
    TwinTypeSpec(
        "pick_and_place",
        TwinTypeKind.DEFINITION,
        "work_product:pick_and_place",
        key_prefix="PNP",
        description="A board's placement file (twin.record_design_files).",
    ),
    TwinTypeSpec(
        "pinmap",
        TwinTypeKind.DEFINITION,
        "work_product:pinmap",
        key_prefix="PIN",
        description=(
            "Which MCU pin does what (twin.record_design_files, or "
            "twin.create_firmware_scaffold from an assembly's joints)."
        ),
    ),
    TwinTypeSpec(
        "firmware_source",
        TwinTypeKind.DEFINITION,
        "work_product:firmware_source",
        key_prefix="FW",
        description=(
            "A firmware project's sources and its build output "
            "(twin.record_design_files, or twin.create_firmware_scaffold)."
        ),
    ),
    # FORGE-711: a third-party part model (an NVIDIA SimReady OpenUSD package)
    # with its preview, recorded by twin.record_design_files from what
    # simready.import wrote. Its own family: it is bought, not authored, so a
    # cad_model slot must not bind to it.
    TwinTypeSpec(
        "purchased_model",
        TwinTypeKind.DEFINITION,
        "work_product:cad_model",
        key_prefix="PMD",
        description=(
            "A bought part's model: an OpenUSD package, its GLB preview, licence and "
            "measured mass (twin.record_design_files after simready.import)."
        ),
    ),
    # G-code for one printer, from slicer.slice or an upload; what
    # library.dispatch_job sends. Slicing again for the same part and printer
    # is its next revision.
    TwinTypeSpec(
        "gcode",
        TwinTypeKind.DEFINITION,
        "work_product:gcode",
        key_prefix="GC",
        description=(
            "G-code for one printer, with the config it was sliced with "
            "(twin.record_design_files after slicer.slice)."
        ),
    ),
    TwinTypeSpec(
        "sketch_strokes",
        TwinTypeKind.DEFINITION,
        "work_product:sketch_strokes",
        key_prefix="SKT",
        description=(
            "A person's freehand sketch on the Create stage (strokes JSON plus an "
            "SVG); each save is the next revision. Not CAD: scale unknown, plane "
            "unassigned."
        ),
    ),
    TwinTypeSpec(
        "sketch_plane",
        TwinTypeKind.DEFINITION,
        "work_product:sketch_plane",
        key_prefix="PLN",
        description=(
            "The plane a sketch is placed on: a right-handed frame, unit and lifecycle "
            "state. One item per sketch; each assignment, reassignment, undo or archive "
            "is the next revision, pinned to the sketch revision it is for."
        ),
    ),
    TwinTypeSpec(
        "sketch_interpretation",
        TwinTypeKind.DEFINITION,
        "work_product:sketch_interpretation",
        key_prefix="SKI",
        description=(
            "The sketch compiler's proposal for a sketch: fitted primitives and proposed, "
            "accepted and rejected constraints, all estimated. Depends on the sketch "
            "revision it was made from. Not CAD and not a measurement."
        ),
    ),
    TwinTypeSpec(
        "parametric_sketch",
        TwinTypeKind.DEFINITION,
        "work_product:parametric_sketch",
        key_prefix="PSK",
        description=(
            "A sketch a person promoted: primitives with only the accepted constraints, "
            "re-checked by the gateway, and a feature plan whose missing depth, angle and "
            "material are required input. Depends on the interpretation and the strokes."
        ),
    ),
    TwinTypeSpec(
        "generated_sketch",
        TwinTypeKind.DEFINITION,
        "work_product:generated_sketch",
        key_prefix="GSK",
        description=(
            "The accepted AI-generated concept sketch for a sketch or a text concept: each "
            "accepted proposal is the next revision, so earlier accepted images stay "
            "available. Illustrative only: not an editable sketch, not CAD, not evidence."
        ),
    ),
    TwinTypeSpec(
        "manufacturing_context",
        TwinTypeKind.DEFINITION,
        "work_product:manufacturing_context",
        key_prefix="MFC",
        description=(
            "The manufacturing context a product (MFC-PROJECT) or one component "
            "(MFC-<component>) is generated against: process, material, quantity, "
            "targets and gaps, each field with its authority and provenance. Every "
            "change is the next revision; each generated concept records the one it used."
        ),
    ),
    TwinTypeSpec(
        "appearance",
        TwinTypeKind.DEFINITION,
        "work_product:appearance",
        key_prefix="APR",
        description=(
            "How the product and each component look (APR-PROJECT): colour, finish, "
            "render material and texture refs, keyed by component name so a regenerated "
            "mesh keeps its look. Never changes CAD or the manufacturing context."
        ),
    ),
    TwinTypeSpec(
        "feature_history_proposal",
        TwinTypeKind.DEFINITION,
        "work_product:feature_history_proposal",
        key_prefix="FHP",
        description=(
            "A proposed editable feature history for one component of an accepted concept "
            "(FHP-<component>): ordered features with each parameter estimated, confirmed or "
            "unknown and its source, assumptions, open questions and the DFM strategy note. "
            "A proposal, not CAD; accepted features become parametric sketches."
        ),
    ),
    TwinTypeSpec(
        "interface_contract",
        TwinTypeKind.DEFINITION,
        "work_product:interface_contract",
        key_prefix="IFC",
        description=(
            "A typed agreement between domains (IFC-CAD-EDA, IFC-EDA-FIRMWARE, "
            "IFC-SIMULATION, IFC-MANUFACTURING): coupled parameters with units, "
            "tolerances, owner and basis, the items implementing each side and the "
            "artifacts expected. Every change is the next revision (the interface "
            "version); proposed until every owning domain agrees by a recorded decision."
        ),
    ),
    TwinTypeSpec(
        "imagine_studio",
        TwinTypeKind.DEFINITION,
        "work_product:imagine_studio",
        key_prefix="IMG",
        description=(
            "The Imagine stage's concept studio (IMG-STUDIO): the formgraph product config "
            "an agent generated, the person's choices, scenarios, assumptions, ledger and "
            "V2 screening results, and the handover to Develop. Every config change or "
            "rework is the next revision; a handed-over revision is read-only."
        ),
    ),
    # -- records -----------------------------------------------------------
    TwinTypeSpec(
        "design_decision",
        TwinTypeKind.RECORD,
        "work_product:design_decision",
        description="A recorded design decision (twin.record_decision).",
    ),
    TwinTypeSpec(
        "simulation_result",
        TwinTypeKind.RECORD,
        "work_product:simulation_result",
        description="One simulation run's result.",
    ),
    TwinTypeSpec(
        "evidence",
        TwinTypeKind.RECORD,
        "engineering_entity:evidence",
        description="Evidence pinned to the revisions it was produced against.",
    ),
    TwinTypeSpec(
        "interface_edge",
        TwinTypeKind.RECORD,
        "work_product:interface_edge",
        description=(
            "A typed coupling between two items (mounts_to, clearance_to, connects_via, "
            "pin_maps_to) pinned to their revisions; proposed, confirmed or removed in place."
        ),
    ),
    TwinTypeSpec(
        "change_impact",
        TwinTypeKind.RECORD,
        "work_product:change_impact",
        description=(
            "One change's cross-domain impact: affected items, stale results and the "
            "proposed reruns with their approval decisions."
        ),
    ),
    TwinTypeSpec(
        "dfm_finding_set",
        TwinTypeKind.RECORD,
        "work_product:dfm_finding_set",
        description=(
            "One component's DFM findings under one process rule profile, pinned to the "
            "geometry and manufacturing context revisions it read: heuristic guidance "
            "(never a pass), or exact checks on a validated B-Rep revision."
        ),
    ),
    TwinTypeSpec(
        "cad_design_loop",
        TwinTypeKind.RECORD,
        "work_product:cad_design_loop",
        description=(
            "One run of the CAD design loop: each iteration's script, build, validity, "
            "measurements, rendered views, critique, DFM findings, model and cost, and the "
            "stop reason. Its result is a proposed CAD revision a person accepts or keeps "
            "the previous one; the critic's verdict is never evidence."
        ),
    ),
    TwinTypeSpec(
        "interface_contract_check",
        TwinTypeKind.RECORD,
        "work_product:interface_contract_check",
        description=(
            "One evaluation of an interface contract: each check satisfied, violated "
            "with values, not evaluated with the missing input, or stale, pinned to the "
            "contract revision and every item revision it read."
        ),
    ),
    TwinTypeSpec(
        "assembly_check",
        TwinTypeKind.RECORD,
        "engineering_entity:assembly_check",
        description=(
            "One design-for-assembly check of an assembly revision (twin.check_assembly): "
            "interference, clearance, hole patterns, outlines, edge connectors, stacks, "
            "joint motion and seated parts, each finding with a severity and the part "
            "revisions involved, pinned to the assembly and every part revision it read."
        ),
    ),
    TwinTypeSpec(
        "dfm_report",
        TwinTypeKind.RECORD,
        "work_product:dfm_report",
        description=(
            "A revision-specific DFM report with its manufacturing release checklist, "
            "pinned to the revisions it read."
        ),
    ),
    TwinTypeSpec(
        "approval",
        TwinTypeKind.RECORD,
        "approvals service",
        description="A gate or tool approval decision.",
    ),
    TwinTypeSpec(
        "session",
        TwinTypeKind.RECORD,
        "agent_sessions (Postgres)",
        description="An agent session and its events.",
    ),
    TwinTypeSpec(
        "run",
        TwinTypeKind.RECORD,
        "runs / design-flow workflow",
        description="One design-flow or chat run.",
    ),
    # -- derived -----------------------------------------------------------
    TwinTypeSpec(
        "prd_view",
        TwinTypeKind.DERIVED,
        "rendered on read (GET /v1/twin/projects/{id}/prd)",
        description=(
            "The prd a reader sees: the prd prose plus intent, needs, objectives "
            "and the current constraint set revision, with KEY@n refs."
        ),
    ),
)

TWIN_TYPES: dict[str, TwinTypeSpec] = {row.name: row for row in _ROWS}


def classify(type_name: str) -> TwinTypeSpec | None:
    """The registry row for ``type_name``, or ``None`` when unclassified."""
    return TWIN_TYPES.get(type_name)


def is_definition(type_name: str) -> bool:
    """True when writes of ``type_name`` create item revisions."""
    spec = TWIN_TYPES.get(type_name)
    return spec is not None and spec.kind is TwinTypeKind.DEFINITION


def definition_types() -> list[str]:
    """All definition type names, in registry order."""
    return [r.name for r in _ROWS if r.kind is TwinTypeKind.DEFINITION]


def family_of(type_name: str) -> set[str]:
    """``type_name`` plus every definition sharing its family."""
    spec = TWIN_TYPES.get(type_name)
    if spec is None or spec.family is None:
        return {type_name}
    return {r.name for r in _ROWS if r.family == spec.family}
