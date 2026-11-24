"""Confine a signed-in account's MCP tool calls to its projects (FORGE-859).

Part A of FORGE-859 made the MCP sign-in establish *who* is calling: with a
provider that implements :class:`~api_gateway.auth.login.PasswordLogin`, each
OAuth token is bound to the verified subject (``sub:<subject>``). This module
is the step that makes that useful on a server shared by several accounts: the
same project scope the gateway applies to HTTP (:mod:`api_gateway.auth.scope`)
is applied to every MCP tool call and resource read.

It is active only when both hold:

* the caller's token is bound to a verified subject (the OAuth login is an
  identity login, ``OAuthConfig.verified_identity``); and
* the deployment's auth provider can answer "which projects may this subject
  touch?" (:func:`~api_gateway.auth.scope.supports_project_scope`).

Everything else is unchanged: a local install (auth off), the shared-secret
login, a static API-key caller and the design-flow service caller
(``METAFORGE_MCP_SERVICE_KEY``) all run exactly as before.

Every registered tool has a :class:`ToolTenancy` in :data:`TOOL_RULES`, the
MCP counterpart of the gateway's ``RouteTenancy``. The rules mirror the
gateway's:

* a project outside the caller's scope is answered exactly as a project that
  does not exist, so ids cannot be probed;
* a tool that names an entity (a twin node, a run, an approval, an agent
  session, an upload, a tool job) is checked against the project that owns
  it, and an entity that belongs to no project is refused;
* a tool with no entry is **refused**. That is the fail-closed default: a
  tool added tomorrow is unavailable to a signed-in account until someone
  decides how it is scoped. ``tests/unit/test_mcp_project_scope.py`` fails
  until every declared tool has an entry.
"""

from __future__ import annotations

import enum
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from metaforge.mcp.gateway_auth import gateway_auth_headers
from observability.tracing import get_tracer

if TYPE_CHECKING:
    from api_gateway.auth.scope import ProjectScope
    from mcp_core.context import McpCallContext

logger = structlog.get_logger(__name__)
tracer = get_tracer("metaforge.mcp.project_scope")

__all__ = [
    "TOOL_RULES",
    "McpProjectScoper",
    "OwnerLookup",
    "ProjectScopeError",
    "ToolRule",
    "ToolTenancy",
    "sidecar_owner_lookups",
]


class ToolTenancy(enum.Enum):
    """How one MCP tool is confined for a signed-in account."""

    #: Names its project: a ``project_id`` argument, or the project the
    #: session is bound to. Required; checked; entity arguments checked too.
    PROJECT = "project"
    #: Names an entity (and possibly a project); each is checked against the
    #: project that owns it.
    ENTITY = "entity"
    #: Answered, then confined to the caller's projects by the handler
    #: (``project.list``, ``project.open``, ``approvals.list``).
    ACCOUNT_LIST = "account_list"
    #: ``project.create``: the new project is registered to the caller.
    ACCOUNT_CREATE = "account_create"
    #: Reference data every account reads: catalogues, the library (seen
    #: through the caller's library view), the deployment's own status.
    SHARED_READ = "shared_read"
    #: Adds to or changes the caller's own library entries (FORGE-876).
    TENANT_LIBRARY = "tenant_library"
    #: Computes over its arguments, or asks an external service; reads and
    #: stores no project data.
    STATELESS = "stateless"
    #: Works on files and sessions in a tool adapter's own work directory
    #: (paths, FreeCAD sessions, generation jobs), not on project data. See
    #: the limits in ``docs/deployment/authentication.md``.
    ADAPTER_WORKSPACE = "adapter_workspace"
    #: Not confined to a project: refused to a signed-in account.
    BLOCKED = "blocked"


#: Entity kinds an argument can name, each with an owner lookup.
#: ``twin_ref`` is a reference field (``depends_on``, ``supersedes`` ...) that
#: may hold item keys as well as node ids: only the node ids in it, anything
#: shaped like a UUID, are checked.
ENTITY_KINDS: frozenset[str] = frozenset(
    {"twin_node", "twin_ref", "run", "approval", "agent_session", "upload", "job"}
)

Anchor = str  # "project" | "entity" | "either" | "none"


@dataclass(frozen=True, slots=True)
class ToolRule:
    """The tenancy of one tool and the arguments that carry its scope."""

    tenancy: ToolTenancy
    #: ``(argument, kind)`` for every argument naming an entity.
    entities: tuple[tuple[str, str], ...] = ()
    #: The argument naming the project (``project.update`` calls it ``id``).
    project_arg: str = "project_id"
    #: What must be present: a project, an entity, either, or nothing.
    anchor: Anchor = "none"
    #: Arguments naming something with no owner lookup. Present: refused.
    unresolvable: tuple[str, ...] = ()
    #: Why a BLOCKED tool is blocked, for the refusal and the docs.
    reason: str = ""


def _project(*entities: tuple[str, str], project_arg: str = "project_id") -> ToolRule:
    return ToolRule(ToolTenancy.PROJECT, entities, project_arg=project_arg, anchor="project")


def _entity(*entities: tuple[str, str], unresolvable: tuple[str, ...] = ()) -> ToolRule:
    return ToolRule(ToolTenancy.ENTITY, entities, anchor="entity", unresolvable=unresolvable)


def _either(*entities: tuple[str, str]) -> ToolRule:
    """A project or one of the entities must be named; all that are, are checked."""
    return ToolRule(ToolTenancy.ENTITY, entities, anchor="either")


def _blocked(reason: str) -> ToolRule:
    return ToolRule(ToolTenancy.BLOCKED, reason=reason)


def _all(rule: ToolRule, *tool_ids: str) -> dict[str, ToolRule]:
    return dict.fromkeys(tool_ids, rule)


_STATELESS = ToolRule(ToolTenancy.STATELESS)
_WORKSPACE = ToolRule(ToolTenancy.ADAPTER_WORKSPACE)
_SHARED = ToolRule(ToolTenancy.SHARED_READ)
_TENANT = ToolRule(ToolTenancy.TENANT_LIBRARY)

_NODE = "twin_node"
_REF = "twin_ref"

_ECT = "an engineering change is not yet traced to its project"
_LOOP = "a design loop is not yet traced to its project"
_TASK = (
    "a phase or review task is not yet traced to its project (the gateway refuses "
    "/v1/client-tasks/{id} and /v1/review-tasks/{id} to a signed-in account too)"
)
_VENDOR = "a vendor quote or order is not yet traced to its project"
_MACHINE_JOB = "machine discovery and jobs act on shared machines (blocked on the gateway too)"
_WHOLE_TWIN = "it searches the whole twin, across every account's projects"
_WHOLE_KNOWLEDGE = "it searches the whole knowledge store, not one project"
_MEMORY = "agent memory is learned across every account's runs"
_SERVER_DISK = "it reads or writes the server's own disk, which every account shares"

#: Every tool's tenancy. A tool missing from here is refused to a signed-in
#: account; the test suite fails until it is added.
TOOL_RULES: dict[str, ToolRule] = {
    # ── accounts and projects ─────────────────────────────────────────
    "project.list": ToolRule(ToolTenancy.ACCOUNT_LIST),
    "project.open": ToolRule(ToolTenancy.ACCOUNT_LIST),
    "project.get": ToolRule(ToolTenancy.ACCOUNT_LIST),
    "project.create": ToolRule(ToolTenancy.ACCOUNT_CREATE),
    "project.update": _project(project_arg="id"),
    "project.delete": _project(project_arg="id"),
    "approvals.list": ToolRule(ToolTenancy.ACCOUNT_LIST),
    "approvals.decide": _entity(("approval_id", "approval")),
    "health.check": _SHARED,
    # ── agent sessions and runs ───────────────────────────────────────
    "session.start": _project(),
    "session.log_event": _entity(("session_id", "agent_session")),
    "session.complete": _entity(("session_id", "agent_session")),
    "run.start_design_flow": _project(),
    "run.get_status": _entity(("run_id", "run")),
    "flow.list": _SHARED,
    "flow.capabilities": ToolRule(ToolTenancy.SHARED_READ, unresolvable=("version_id",)),
    "flow.compile_intent": _STATELESS,
    "flow.propose": _project(),
    "flow.start_run": _project(),
    "flow.status": _entity(("run_id", "run")),
    "flow.lifecycle": _entity(("run_id", "run")),
    "flow.verify_completion": _entity(("run_id", "run")),
    "flow.await_gate": _entity(("run_id", "run")),
    "flow.supervise": _entity(("run_id", "run")),
    "flow.patch": _entity(("run_id", "run"), unresolvable=("version_id",)),
    "phase.list_tasks": _project(("run_id", "run")),
    "phase.claim": _blocked(_TASK),
    "phase.submit": _blocked(_TASK),
    "review.list_tasks": _project(),
    "review.claim": _blocked(_TASK),
    "review.submit": _blocked(_TASK),
    # ── files ─────────────────────────────────────────────────────────
    "files.import": _project(),
    "files.request_upload": _project(),
    "files.list": _entity(("work_product_id", _NODE)),
    "files.read": _entity(("work_product_id", _NODE)),
    "files.link": _entity(("work_product_id", _NODE)),
    "files.upload_status": _entity(("upload_id", "upload")),
    "files.decide_upload": _entity(("upload_id", "upload")),
    "files.import_local": _blocked(_SERVER_DISK),
    "files.save_local": _blocked(_SERVER_DISK),
    # ── knowledge, memory, constraints ────────────────────────────────
    "knowledge.search": _project(),
    "knowledge.ingest": _project(("source_work_product_id", _NODE)),
    "knowledge.extract": _blocked(_WHOLE_KNOWLEDGE),
    "knowledge.populate_bom": _blocked(_WHOLE_KNOWLEDGE),
    "memory.list_insights": _blocked(_MEMORY),
    "memory.retrieve_similar_experience": _blocked(_MEMORY),
    "constraint.validate": _entity(("work_product_ids", _NODE)),
    # ── vendors ───────────────────────────────────────────────────────
    "vendor.list_connectors": _SHARED,
    "vendor.quote": _project(),
    "vendor.handoffs": _entity(("work_product_ids", _NODE)),
    "vendor.place_order": _blocked(_VENDOR),
    "vendor.order_status": _blocked(_VENDOR),
    # ── the library (FORGE-876): shared entries plus the account's own ─
    **_all(
        _SHARED,
        "library.get_appearance",
        "library.get_component",
        "library.get_ergonomic_limit",
        "library.get_machine",
        "library.get_material",
        "library.get_organisation",
        "library.get_process",
        "library.list_appearances",
        "library.list_components",
        "library.list_ergonomic_limits",
        "library.list_machines",
        "library.list_materials",
        "library.list_organisations",
        "library.list_processes",
        "library.machine_limits",
        "library.process_limits",
        "library.search",
        "component.find",
        "library.standard_part",
        "library.resolve_component_model",
    ),
    **_all(
        _TENANT,
        "library.register_appearance",
        "library.register_component",
        "library.register_ergonomic_limit",
        "library.register_machine",
        "library.register_material",
        "library.register_organisation",
        "component.adopt",
        "library.register_process",
        "library.update_appearance",
        "library.update_component",
        "twin.confirm_estimate",
        "library.update_ergonomic_limit",
        "library.update_machine",
        "library.update_material",
        "library.update_organisation",
        "library.update_process",
    ),
    "library.discover_machine": _blocked(_MACHINE_JOB),
    "library.dispatch_job": _blocked(_MACHINE_JOB),
    "library.job_status": _blocked(_MACHINE_JOB),
    "library.record_job_result": _blocked(_MACHINE_JOB),
    # ── the digital twin ──────────────────────────────────────────────
    "twin.get_node": _either(("node_id", _NODE)),
    "twin.item_history": _either(("node_id", _NODE)),
    "twin.thread_for": _entity(("node_id", _NODE)),
    "twin.what_verifies": _entity(("node_id", _NODE)),
    "twin.where_used": _entity(("node_id", _NODE)),
    "twin.find_by_property": _blocked(_WHOLE_TWIN),
    "twin.query_cypher": _blocked(_WHOLE_TWIN),
    "twin.constraint_violations": _project(),
    "twin.get_harness_estimate": _entity(("work_product_id", _NODE)),
    "twin.stage_work_product_file": _entity(("node_id", _NODE)),
    "twin.export_cad_step": _entity(("node_id", _NODE)),
    "twin.job_status": _entity(("job_id", "job")),
    "twin.approve_engineering_entity": _entity(("entity_id", _NODE)),
    "twin.approve_release": _project(("entity_id", _NODE)),
    "twin.accept_release_finding": _project(),
    "twin.attempt_promotion": _project(),
    "twin.create_baseline": _project(),
    "twin.create_release_package": _project(),
    "twin.get_release_checklist": _project(),
    "twin.generate_test_plan": _project(),
    "twin.create_bringup_checklist": _either(("work_product_id", _NODE)),
    "twin.create_firmware_scaffold": _either(("work_product_id", _NODE)),
    "twin.build_assembly": _project(),
    "twin.check_assembly": _project(),
    "twin.place_part": _either(("node_id", _NODE)),
    "twin.build_exact_cad": _project(),
    "twin.design_cad_loop": _project(),
    "twin.evaluate_dfm": _project(),
    "twin.measure_dfm": _either(("node_id", _NODE)),
    "twin.get_dfm_findings": _project(),
    "twin.get_dfm_report": _project(),
    "twin.get_manufacturing_context": _project(),
    "twin.state_manufacturing_context": _project(),
    "twin.evaluate_metric": _either(("work_product_id", _NODE)),
    "twin.evaluate_overhang_metric": _either(("work_product_id", _NODE)),
    "twin.evaluate_thermal_metric": _either(("work_product_id", _NODE)),
    "twin.optimize_parameter": _either(("work_product_id", _NODE)),
    "twin.rank_sensitivity": _either(("work_product_id", _NODE)),
    "twin.run_item_fea": _either(("node_id", _NODE)),
    "twin.render_work_product": _either(("work_product_id", _NODE)),
    "twin.delete_work_product": _entity(("work_product_id", _NODE)),
    "twin.start_design_loop": _either(("work_product_id", _NODE)),
    "twin.start_tube_height_design_loop": _either(("work_product_id", _NODE)),
    "twin.get_design_loop": _blocked(_LOOP),
    "twin.approve_design_loop": _blocked(_LOOP),
    "twin.propose_engineering_change": _project(),
    "twin.analyze_engineering_change": _blocked(_ECT),
    "twin.approve_engineering_change": _blocked(_ECT),
    "twin.commit_engineering_change": _blocked(_ECT),
    "twin.execute_revalidation_plan": _blocked(_ECT),
    "twin.mark_engineering_change_rolled_back": _blocked(_ECT),
    "twin.reject_engineering_change": _blocked(_ECT),
    "twin.propose_change": _project(("work_products_affected", _REF)),
    "twin.commit_geometry": _project(
        ("part_node_ids", _REF), ("source_node_ids", _REF), ("supersedes", _REF)
    ),
    "twin.commit_design_sketch": _project(("source_node_ids", _REF)),
    "twin.commit_compliance_checklist": _project(),
    "twin.commit_hazard_analysis": _project(),
    "twin.commit_procurement_record": _project(("source_node_ids", _REF)),
    "twin.commit_system_architecture": _project(),
    "twin.commit_technical_drawing": _project(("source_node_ids", _REF)),
    "twin.import_urdf": _project(),
    "twin.record_claim": _project(),
    "twin.record_component_selection": _project(("supersedes", _REF)),
    "twin.record_constraint_set": _project(("supersedes", _REF)),
    "twin.record_decision": _project(
        ("depends_on", _REF),
        ("evidence_refs", _REF),
        ("parent_refs", _REF),
        ("supersedes", _REF),
    ),
    "twin.record_design_files": _project(("source_node_ids", _REF), ("supersedes", _REF)),
    "twin.record_document": _project(
        ("analysed_geometry_node_id", _REF),
        ("evidence_node_id", _REF),
        ("source_part_node_ids", _REF),
        ("depends_on", _REF),
    ),
    "twin.record_engineering_entity": _project(("parent_refs", _REF), ("supersedes", _REF)),
    "twin.record_evidence": _project(
        ("contradicts", _REF),
        ("depends_on", _REF),
        ("supports", _REF),
        ("supersedes", _REF),
    ),
    "twin.record_hierarchy_node": _project(
        ("instance_of_node_id", _REF), ("parent_id", _REF), ("realized_by_node_id", _REF)
    ),
    "twin.record_measurement": _project(("work_product_id", _NODE), ("device_instance_id", _REF)),
    "twin.register_device_instance": _project(),
    "twin.realize_hierarchy_node": _entity(
        ("hierarchy_node_id", _NODE), ("work_product_id", _NODE), ("bom_item_id", _REF)
    ),
    "twin.compute_hierarchy_rollup": _entity(("root_id", _NODE)),
    "twin.select_component": _project(),
    "twin.select_concept": _project(),
    "twin.generate_sketch": _project(),
    "twin.interpret_sketch": _either(("work_product_id", _NODE)),
    "twin.confirm_sketch_reading": _entity(("node_id", _NODE)),
    "twin.render_concept_sketch": _project(("source_node_ids", _REF)),
    "twin.render_concept_image": _either(("layout_node_id", _NODE)),
    "twin.render_structured_mesh": _either(("layout_node_id", _NODE)),
    "twin.critique_concept_image": _entity(("node_id", _NODE)),
    "twin.generate_concept_mesh": _either(("image_node_id", _NODE)),
    "twin.segment_concept_mesh": _entity(("node_id", _NODE)),
    "twin.complete_concept_part": _entity(("node_id", _NODE)),
    "twin.regenerate_concept_part": _entity(("node_id", _NODE), ("image_node_id", _NODE)),
    "twin.resolve_concept_component": _project(),
    "twin.imagine_get_studio": _project(),
    "twin.imagine_put_studio": _project(),
    "twin.imagine_patch_studio": _project(),
    "twin.imagine_adopt": _project(),
    "twin.component_thread": _project(),
    "twin.form_thread": _project(),
    "twin.imagine_accept_form": _project(),
    "twin.imagine_add_mesh_reference": _project(),
    "twin.imagine_look": _project(),
    "twin.imagine_validate": _project(),
    "twin.imagine_accept": _project(),
    "twin.imagine_rework": _project(),
    "twin.imagine_handover": _project(),
    "twin.imagine_handover_status": _project(),
    "twin.imagine_resolve_part": _STATELESS,
    # ── computation over the arguments, and external catalogues ───────
    **_all(
        _STATELESS,
        "aircraft.cruise_performance",
        "aircraft.electric_endurance",
        "aircraft.trim_stability",
        "aircraft.wing_sizing",
        "packaging.box_compression",
        "packaging.rsc_dieline",
        "robot.diff_drive_kinematics",
        "robot.size_drive",
        "power.check_budget",
        "power.runtime",
        "cost.estimate_manufacturing",
        "optimize.suggest",
        "calculix.check_mesh_convergence",
        "calculix.compute_joint_loads",
        "calculix.cross_check_cantilever_beam",
        "calculix.cross_check_cantilever_frequency",
        "calculix.cross_check_thermal_steady_state",
        "cadquery.describe_faces",
        "cadquery.dfm_measure",
        "cadquery.export_step",
        "cadquery.validate_shape",
        "distributors.resolve_offers",
        "web.extract",
        "web.fetch",
        "web.search",
        "simready.search",
        "simready.inspect",
        "tripo.get_balance",
        *(
            f"{distributor}.{op}"
            for distributor in ("arrow", "digikey", "farnell", "mouser", "nexar")
            for op in ("search", "get_product", "get_pricing", "get_availability")
        ),
    ),
    **_all(
        _SHARED,
        "component.search_intent",
        "component.search_parametric",
        "kicad.search_library",
        "kicad.describe_library_component",
        "slicer.list_profiles",
    ),
    # ── tool adapters' own work directories ───────────────────────────
    **_all(
        _WORKSPACE,
        "blender.export_blend",
        "blender.render",
        "cadquery.boolean_operation",
        "cadquery.check_interference",
        "cadquery.create_assembly",
        "cadquery.create_parametric",
        "cadquery.execute_script",
        "cadquery.export_geometry",
        "cadquery.export_sdf",
        "cadquery.export_sdf_assembly",
        "cadquery.export_urdf",
        "cadquery.export_urdf_assembly",
        "cadquery.export_usd",
        "cadquery.export_usd_assembly",
        "cadquery.generate_enclosure",
        "cadquery.generate_ros2_launch",
        "cadquery.get_properties",
        "cadquery.validate_physics_stability",
        "calculix.extract_results",
        "calculix.run_fea",
        "calculix.run_thermal",
        "calculix.validate_mesh",
        "firmware.build",
        "firmware.run_emulated",
        "firmware.static_check",
        "gazebo.extract_results",
        "gazebo.run_simulation",
        "gazebo.validate_world",
        "isaac_sim.render_scene",
        "isaac_sim.run_physics",
        "kicad.autoroute_pcb",
        "kicad.edit_pcb",
        "kicad.export_3d",
        "kicad.export_bom",
        "kicad.export_drill",
        "kicad.export_gerber",
        "kicad.export_netlist",
        "kicad.export_pos",
        "kicad.export_svg",
        "kicad.generate_pcb",
        "kicad.generate_schematic",
        "kicad.get_pin_mapping",
        "kicad.render_pcb",
        "kicad.run_drc",
        "kicad.run_erc",
        "kicad.update_components",
        "meshy.download",
        "meshy.get_task",
        "meshy.image_to_3d",
        "meshy.refine_texture",
        "meshy.text_to_3d",
        "omniverse_usd.convert_glb_to_usd",
        "omniverse_usd.describe_stage",
        "omniverse_usd.validate_usd_minimum",
        "openfoam.run_internal_flow",
        "render.model_views",
        "simready.attribution",
        "simready.import",
        "slant3d.analyze_part",
        "slant3d.get_part",
        "slant3d.list_filaments",
        "slant3d.quote",
        "slicer.slice",
        "spice.run_simulation",
        "tripo.convert",
        "tripo.download",
        "tripo.get_task",
        "tripo.image_to_3d",
        "tripo.text_to_3d",
        "zoo.convert",
        "zoo.get_conversion",
        "zoo.mass_properties",
        *(
            f"freecad.{name}"
            for name in (
                "add_assembly_joint",
                "add_part_to_assembly",
                "boolean",
                "boolean_operation",
                "chamfer",
                "chamfer_edges",
                "close_session",
                "create_assembly",
                "create_body",
                "create_parametric",
                "create_primitive",
                "create_sketch",
                "create_variable_set",
                "describe_model",
                "describe_session",
                "describe_step_file",
                "execute_code",
                "export_geometry",
                "export_model",
                "fastener_hole",
                "fillet",
                "fillet_edges",
                "generate_enclosure",
                "generate_gear",
                "generate_ic_package",
                "generate_mesh",
                "generate_profile_part",
                "get_properties",
                "import_step",
                "lattice_perforation",
                "linear_pattern",
                "list_joints",
                "list_named_faces",
                "loft_sketches",
                "measure",
                "mirror_feature",
                "open_session",
                "pad_sketch",
                "pocket_sketch",
                "polar_pattern",
                "revolve_sketch",
                "set_expression",
                "shell_solid",
                "sweep_sketch",
                "thread_insert",
                "transform_object",
            )
        ),
    ),
}

#: What a tool with no rule gets: refused, naming the tool.
_UNCLASSIFIED = _blocked("it has not been classified for a server shared by several accounts")


def rule_for(tool_id: str) -> ToolRule:
    """The tool's rule, or the fail-closed default."""
    return TOOL_RULES.get(tool_id, _UNCLASSIFIED)


class ProjectScopeError(RuntimeError):
    """A signed-in account's call that its project scope does not allow."""

    def __init__(
        self, tool_id: str, reason: str, *, code: str = "not_found", retryable: bool = False
    ) -> None:
        self.tool_id = tool_id
        self.reason = reason
        self.code = code
        self.retryable = retryable
        super().__init__(f"{tool_id}: {reason}")


#: ``entity_id -> owning project id, or None`` (None: unknown, or no project).
OwnerLookup = Callable[[str], Awaitable[str | None]]

_UUIDISH = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_UUID_IN_TEXT = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _ids(value: Any) -> list[str] | None:
    """The ids in an entity argument: a string or a list of strings."""
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [v for v in value if v]
    return None


def _refs(value: Any) -> list[str]:
    """Every UUID-shaped string anywhere in a reference argument."""
    found: list[str] = []
    if isinstance(value, str):
        found.extend(_UUID_IN_TEXT.findall(value))
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(_refs(item))
    elif isinstance(value, list | tuple):
        for item in value:
            found.extend(_refs(item))
    return found


@dataclass(frozen=True, slots=True)
class Admission:
    """A call the scope allows, and how to run it."""

    tenancy: ToolTenancy
    arguments: dict[str, Any]
    context: McpCallContext
    library_view: str | None
    subject: str


class McpProjectScoper:
    """Applies a provider's project scope to MCP calls from signed-in accounts."""

    def __init__(
        self,
        authorizer: Any,
        lookups: dict[str, OwnerLookup],
        *,
        library_in_process: bool = True,
        project_deleter: Callable[[str], Awaitable[Any]] | None = None,
        metrics: Any = None,
        ttl_seconds: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._authorizer = authorizer
        self._lookups = lookups
        self._library_in_process = library_in_process
        self._project_deleter = project_deleter
        self._metrics = metrics
        self._ttl = ttl_seconds
        self._clock = clock
        self._cache: dict[str, tuple[float, ProjectScope]] = {}

    # ── who is confined ──────────────────────────────────────────────

    @staticmethod
    def applies_to(ctx: McpCallContext) -> bool:
        """A call confined to an account: one the server signed in itself.

        ``actor_verified`` is set only for an OAuth token on an identity
        login. The design-flow service caller, an API-key caller and the
        shared-secret login never have it, and keep today's behaviour.
        """
        return bool(ctx.actor_verified) and not ctx.service_verified

    async def scope_for(self, ctx: McpCallContext, tool_id: str = "call") -> ProjectScope:
        """The caller's scope, from the provider (cached for a few seconds)."""
        from metaforge.mcp.oauth import subject_of

        subject = subject_of(ctx.actor_id)
        if subject is None:
            # A token from before the identity login (persisted across a
            # restart), or anything else not bound to a person: it cannot be
            # shown to belong to any account, so it reaches nothing.
            raise self._refuse(
                tool_id,
                "this sign-in is not bound to an account; sign in again",
                code="not_signed_in",
                tenancy=None,
            )
        now = self._clock()
        cached = self._cache.get(subject)
        if cached is not None and cached[0] > now:
            return cached[1]
        from api_gateway.auth.principal import Principal
        from api_gateway.auth.provider import AuthUnavailable

        try:
            scope: ProjectScope = await self._authorizer.project_scope(Principal(subject=subject))
        except AuthUnavailable as exc:
            logger.error("mcp_project_scope_unavailable", tool_id=tool_id, error=str(exc))
            raise self._refuse(
                tool_id,
                "authorization is temporarily unavailable; try again",
                code="scope_unavailable",
                retryable=True,
                tenancy=None,
            ) from exc
        self._cache[subject] = (now + self._ttl, scope)
        if len(self._cache) > 1024:
            self._cache.pop(next(iter(self._cache)))
        return scope

    def forget(self, subject: str) -> None:
        self._cache.pop(subject, None)

    # ── tool calls ───────────────────────────────────────────────────

    async def admit(
        self, tool_id: str, arguments: dict[str, Any], ctx: McpCallContext
    ) -> Admission | None:
        """Check one tool call. ``None``: not confined, run it as before.

        Raises :class:`ProjectScopeError` when the call leaves the scope.
        """
        if not self.applies_to(ctx):
            return None
        from metaforge.mcp.oauth import subject_of

        rule = rule_for(tool_id)
        with tracer.start_as_current_span("mcp.project_scope") as span:
            span.set_attribute("mcp.tool_id", tool_id)
            span.set_attribute("mcp.project_scope.tenancy", rule.tenancy.value)
            scope = await self.scope_for(ctx, tool_id)
            subject = subject_of(ctx.actor_id) or ""
            if scope.unrestricted:
                span.set_attribute("mcp.project_scope.outcome", "unrestricted")
                return None
            arguments = dict(arguments)
            await self._check(tool_id, rule, arguments, ctx, scope)
            span.set_attribute("mcp.project_scope.outcome", "allowed")
        if rule.tenancy is ToolTenancy.ACCOUNT_LIST:
            logger.info("mcp_project_scope_list_confined", tool_id=tool_id, subject=subject)
            self._count("filtered", rule.tenancy)
        return Admission(
            tenancy=rule.tenancy,
            arguments=arguments,
            context=ctx.model_copy(update={"allowed_projects": frozenset(scope.project_ids)}),
            library_view=self._library_view(scope),
            subject=subject,
        )

    async def _check(
        self,
        tool_id: str,
        rule: ToolRule,
        arguments: dict[str, Any],
        ctx: McpCallContext,
        scope: ProjectScope,
    ) -> None:
        tenancy = rule.tenancy
        if tenancy is ToolTenancy.BLOCKED:
            raise self._refuse(
                tool_id,
                f"not available to a signed-in account on a shared server: {rule.reason}",
                code="tool_not_scoped",
                tenancy=tenancy,
            )
        # A library served by the gateway gets the account's view forwarded with
        # each call (remote_library.library_view_headers), so it is admitted on
        # the same terms as an in-process one: the shared entries and the
        # account's own to read, the account's own to write.
        if tenancy is ToolTenancy.TENANT_LIBRARY and not scope.tenant:
            raise self._refuse(
                tool_id,
                "your account cannot add to the library on this server",
                code="library_read_only",
                tenancy=tenancy,
            )
        for name in rule.unresolvable:
            if arguments.get(name) not in (None, ""):
                raise self._refuse(
                    tool_id,
                    f"'{name}' cannot be checked against your projects on a shared server",
                    code="tool_not_scoped",
                    tenancy=tenancy,
                )

        # The project the session or the transport bound, if any: a handler
        # with no project argument falls back to it, so it is checked for
        # every tool, whatever the tenancy.
        bound = _context_project(ctx)
        if bound is not None and not scope.allows(bound):
            raise self._not_found(tool_id, "project", bound, tenancy)

        explicit = arguments.get(rule.project_arg) if tenancy is not ToolTenancy.STATELESS else None
        if explicit not in (None, ""):
            if not isinstance(explicit, str) or not _UUIDISH.match(explicit):
                raise self._not_found(tool_id, "project", str(explicit), tenancy)
            if not scope.allows(explicit):
                raise self._not_found(tool_id, "project", explicit, tenancy)
        elif explicit is None and bound is not None and rule.project_arg == "project_id":
            # The handler would read the bound project; name it, so a handler
            # that treats a missing project as "every project" cannot.
            # Only tools that take one: the rest would reject an extra argument.
            if rule.anchor in ("project", "either"):
                arguments["project_id"] = bound
        has_project = arguments.get(rule.project_arg) not in (None, "")

        checked = 0
        for name, kind in rule.entities:
            value = arguments.get(name)
            if value in (None, "", []):
                continue
            ids = _refs(value) if kind == "twin_ref" else _ids(value)
            if ids is None:
                raise self._not_found(tool_id, name, "?", tenancy)
            lookup_kind = "twin_node" if kind == "twin_ref" else kind
            for entity_id in ids:
                if not _UUIDISH.match(entity_id):
                    raise self._not_found(tool_id, name, entity_id, tenancy)
                owner = await self._owner(tool_id, lookup_kind, entity_id, tenancy)
                if owner is None and await self._own_request(
                    tool_id, lookup_kind, entity_id, ctx, tenancy
                ):
                    # The caller's own request with no project yet (a held
                    # project.create): theirs to decide, nobody else's.
                    checked += 1
                    continue
                if owner is None or not scope.allows(owner):
                    raise self._not_found(tool_id, name, entity_id, tenancy)
                checked += 1

        if rule.anchor == "project" and not has_project:
            raise self._refuse(
                tool_id,
                "project_id is required on a server shared by several accounts "
                "(pass it, or open a project with project.open or session.start first)",
                code="project_required",
                tenancy=tenancy,
            )
        if rule.anchor == "entity" and checked == 0:
            names = ", ".join(n for n, _ in rule.entities) or "an entity id"
            raise self._refuse(
                tool_id,
                f"{names} is required on a server shared by several accounts",
                code="entity_required",
                tenancy=tenancy,
            )
        if rule.anchor == "either" and checked == 0 and not has_project:
            names = " or ".join(["project_id", *(n for n, _ in rule.entities)])
            raise self._refuse(
                tool_id,
                f"{names} is required on a server shared by several accounts",
                code="project_required",
                tenancy=tenancy,
            )

    async def _owner(
        self, tool_id: str, kind: str, entity_id: str, tenancy: ToolTenancy
    ) -> str | None:
        lookup = self._lookups.get(kind)
        if lookup is None:
            raise self._refuse(
                tool_id,
                f"cannot check which project this {kind.replace('_', ' ')} belongs to here",
                code="scope_unavailable",
                tenancy=tenancy,
            )
        return await self._lookup(tool_id, kind, lookup, entity_id, tenancy)

    async def _lookup(
        self,
        tool_id: str,
        kind: str,
        lookup: OwnerLookup,
        entity_id: str,
        tenancy: ToolTenancy,
    ) -> str | None:
        with tracer.start_as_current_span("mcp.entity_owner") as span:
            span.set_attribute("entity.kind", kind)
            try:
                owner = await lookup(entity_id)
            except Exception as exc:  # a lookup failing is not "not yours"
                span.record_exception(exc)
                logger.warning(
                    "mcp_project_scope_lookup_failed", tool_id=tool_id, kind=kind, error=str(exc)
                )
                raise self._refuse(
                    tool_id,
                    "could not check which project this belongs to; try again",
                    code="scope_unavailable",
                    retryable=True,
                    tenancy=tenancy,
                ) from exc
            span.set_attribute("entity.resolved", owner is not None)
        return owner

    async def _own_request(
        self,
        tool_id: str,
        kind: str,
        entity_id: str,
        ctx: McpCallContext,
        tenancy: ToolTenancy,
    ) -> bool:
        """Whether an entity with no project is an approval this caller asked for."""
        from mcp_core.context import is_requester

        lookup = self._lookups.get(f"{kind}_requester")
        if lookup is None:
            return False
        requester = await self._lookup(tool_id, f"{kind}_requester", lookup, entity_id, tenancy)
        if not is_requester(requester, ctx.actor_id):
            return False
        logger.info("mcp_project_scope_own_request", tool_id=tool_id, kind=kind)
        return True

    async def after(self, tool_id: str, admission: Admission, result: Any) -> Any:
        """``project.create``: register the new project to its creator.

        If the provider cannot, the project is deleted again, as the gateway
        does: a project nobody can reach is worse than a failed call.
        """
        if admission.tenancy is not ToolTenancy.ACCOUNT_CREATE:
            return result
        # Adapters answer in an envelope ({"status", "data", ...}).
        data = result.get("data", result) if isinstance(result, dict) else None
        project_id = data.get("id") if isinstance(data, dict) else None
        if not project_id:
            return result
        from api_gateway.auth.principal import Principal
        from mcp_core.context import clear_session_project

        try:
            await self._authorizer.project_created(
                Principal(subject=admission.subject), str(project_id)
            )
        except Exception as exc:
            logger.error("mcp_project_registration_failed", project_id=project_id, error=str(exc))
            if self._project_deleter is not None:
                try:
                    await self._project_deleter(str(project_id))
                except Exception as cleanup:  # noqa: BLE001 - report the first failure
                    logger.error(
                        "mcp_project_registration_cleanup_failed",
                        project_id=project_id,
                        error=str(cleanup),
                    )
            if admission.context.session_is_stable:
                clear_session_project(admission.context.session_id)
            raise self._refuse(
                tool_id,
                "the project could not be registered to your account; nothing was created",
                code="registration_failed",
                retryable=True,
                tenancy=admission.tenancy,
            ) from exc
        self.forget(admission.subject)
        logger.info("mcp_project_registered", project_id=project_id, subject=admission.subject)
        return result

    # ── resources and the HTTP side routes ───────────────────────────

    async def admit_resource(self, uri: str, ctx: McpCallContext) -> McpCallContext | None:
        """Check a ``resources/read``. ``None``: not confined."""
        if not self.applies_to(ctx):
            return None
        from mcp_core.resources import ResourceUriError, parse_resource_uri

        scope = await self.scope_for(ctx, "resources/read")
        if scope.unrestricted:
            return None
        bound = _context_project(ctx)
        if bound is not None and not scope.allows(bound):
            raise self._not_found("resources/read", "project", bound, None)
        try:
            parsed = parse_resource_uri(uri)
        except ResourceUriError:
            return ctx.model_copy(update={"allowed_projects": frozenset(scope.project_ids)})
        parts = parsed.path.strip("/").split("/")
        owner: str | None
        if parsed.adapter == "health":
            owner = None
        elif parsed.adapter == "twin" and len(parts) == 2:
            owner = parts[1]
        elif parsed.adapter == "flow" and len(parts) == 2 and parts[0] == "run":
            owner = await self._owner("resources/read", "run", parts[1], ToolTenancy.ENTITY)
        elif parsed.adapter == "files" and parts and parts[0]:
            owner = await self._owner("resources/read", "twin_node", parts[0], ToolTenancy.ENTITY)
        else:
            raise self._refuse(
                "resources/read",
                f"{uri} is not available to a signed-in account on a shared server",
                code="tool_not_scoped",
                tenancy=ToolTenancy.BLOCKED,
            )
        if parsed.adapter != "health" and (owner is None or not scope.allows(owner)):
            raise self._not_found("resources/read", "resource", uri, ToolTenancy.ENTITY)
        return ctx.model_copy(update={"allowed_projects": frozenset(scope.project_ids)})

    async def visible_projects(self, ctx: McpCallContext) -> frozenset[str] | None:
        """The projects a confined caller may see, ``None`` when not confined.

        Never raises: used to trim notices, where an unavailable answer
        means "show nothing", not "fail the call".
        """
        if not self.applies_to(ctx):
            return None
        try:
            scope = await self.scope_for(ctx, "notice")
        except ProjectScopeError:
            return frozenset()
        return None if scope.unrestricted else frozenset(scope.project_ids)

    @contextmanager
    def running(self, admission: Admission) -> Iterator[None]:
        """Run an admitted call under the caller's projects and library view."""
        from mcp_core.context import with_context
        from twin_core.library.viewer import library_viewer

        with with_context(admission.context), library_viewer(admission.library_view):
            yield

    # ── internals ────────────────────────────────────────────────────

    @staticmethod
    def _library_view(scope: ProjectScope) -> str | None:
        from twin_core.library.viewer import NO_TENANT

        return None if scope.unrestricted else (scope.tenant or NO_TENANT)

    def _not_found(
        self, tool_id: str, what: str, value: str, tenancy: ToolTenancy | None
    ) -> ProjectScopeError:
        # The same words whether it is someone else's or does not exist.
        return self._refuse(
            tool_id, f"{what} {value!r} was not found", code="not_found", tenancy=tenancy
        )

    def _refuse(
        self,
        tool_id: str,
        reason: str,
        *,
        code: str,
        tenancy: ToolTenancy | None,
        retryable: bool = False,
    ) -> ProjectScopeError:
        logger.info(
            "mcp_project_scope_refused",
            tool_id=tool_id,
            code=code,
            tenancy=tenancy.value if tenancy else None,
        )
        self._count(code, tenancy)
        return ProjectScopeError(tool_id, reason, code=code, retryable=retryable)

    def _count(self, outcome: str, tenancy: ToolTenancy | None) -> None:
        if self._metrics is None:
            return
        try:
            self._metrics.record_mcp_project_scope(outcome, tenancy.value if tenancy else "unknown")
        except Exception:  # noqa: BLE001 - metrics never fail a call
            pass


def _context_project(ctx: McpCallContext) -> str | None:
    """The project the transport, the session or a session handle bound."""
    from mcp_core.context import bound_project

    project = ctx.project_id
    if project is None and ctx.session_is_stable:
        project = bound_project(ctx.session_id)
    return str(project) if project else None


# ── owner lookups for the MCP sidecar ─────────────────────────────────


def _str(value: object) -> str | None:
    return str(value) if value else None


def sidecar_owner_lookups(
    *,
    twin: Any = None,
    agent_session_store: Any = None,
    approvals_lister: Any = None,
    gateway_url: str | None = None,
    http_client: Any = None,
) -> dict[str, OwnerLookup]:
    """Owner lookups from what the sidecar has: its twin and session store in
    process, runs and uploads through the gateway, approvals through the
    approvals binding, tool jobs from this process's job runner.

    A kind without its source has no lookup, and a call naming one is refused.
    """
    lookups: dict[str, OwnerLookup] = {}

    if twin is not None:

        async def twin_node(node_id: str) -> str | None:
            try:
                uid = uuid.UUID(node_id)
            except ValueError:
                return None
            node = await twin.get_work_product(uid)
            if node is None:
                get_entity = getattr(twin, "get_engineering_entity", None)
                node = await get_entity(uid) if get_entity else None
            return _str(getattr(node, "project_id", None)) if node is not None else None

        lookups["twin_node"] = twin_node

    if agent_session_store is not None:

        async def agent_session(session_id: str) -> str | None:
            session = await agent_session_store.get_session(session_id)
            return _str(getattr(session, "project_id", None)) if session is not None else None

        lookups["agent_session"] = agent_session

    if approvals_lister is not None:

        async def _approval_item(approval_id: str) -> dict[str, Any] | None:
            for item in await approvals_lister(None):
                if item.get("id") == approval_id:
                    return item if isinstance(item, dict) else None
            return None

        async def approval(approval_id: str) -> str | None:
            item = await _approval_item(approval_id)
            return _str(item.get("project_id")) if item is not None else None

        async def approval_requester(approval_id: str) -> str | None:
            # Who asked, for an approval with no project yet; only the
            # gateway's established requester (``requester``), never the
            # display label ``requested_by``.
            item = await _approval_item(approval_id)
            if item is None or item.get("project_id"):
                return None
            return _str(item.get("requester"))

        lookups["approval"] = approval
        lookups["approval_requester"] = approval_requester

    async def job(job_id: str) -> str | None:
        from tool_registry.jobs import active_job_runner

        runner = active_job_runner()
        record = runner.get(job_id) if runner is not None else None
        return _str(getattr(record, "project_id", None)) if record is not None else None

    lookups["job"] = job

    if gateway_url:
        base = gateway_url.rstrip("/")

        async def _get(path: str) -> dict[str, Any] | None:
            import httpx

            owned = http_client is None
            http = http_client or httpx.AsyncClient(timeout=10.0, headers=gateway_auth_headers())
            try:
                response = await http.get(f"{base}{path}")
            finally:
                if owned:
                    await http.aclose()
            if response.status_code == 404:
                return None
            response.raise_for_status()
            body = response.json()
            return body if isinstance(body, dict) else None

        async def run(run_id: str) -> str | None:
            from api_gateway.runs.schemas import project_of

            body = await _get(f"/v1/runs/{run_id}")
            request = (body or {}).get("request")
            return project_of(request) if isinstance(request, dict) else None

        async def upload(upload_id: str) -> str | None:
            body = await _get(f"/v1/uploads/{upload_id}")
            return _str((body or {}).get("project_id"))

        lookups["run"] = run
        lookups["upload"] = upload

    return lookups
