"""Print orientation, the slicer cross-check and the manufacturing MCP tools.

* A print orientation in the manufacturing context (per component, stated
  or assumed) drives the exact FDM checks; a request's direction still
  overrides it; changing it makes the exact findings stale through the
  recompute path.
* The slicer cross-check is recorded as evidence on the exact finding set,
  never changes its status, and is ``not_evaluated`` with the reason when the
  slicer cannot run.
* ``twin.get_manufacturing_context``, ``twin.state_manufacturing_context``,
  ``twin.get_dfm_report`` and ``twin.get_release_checklist``: an agent only
  proposes; only a person's approved call is ``user_stated``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from api_gateway.library import routes as library_routes
from api_gateway.operations import publisher as ops
from api_gateway.twin import dfm_exact, dfm_report, dfm_slicer
from api_gateway.twin import dfm_findings as dfm
from api_gateway.twin import manufacturing_context as mc
from api_gateway.twin import manufacturing_tools as tools
from mcp_core.annotations import annotations_for
from mcp_core.guardrails import (
    APPROVED_BY_ARG,
    Caller,
    decide,
    records_approver,
    requires_human_authority,
    strip_reserved_arguments,
)
from tests.unit.test_dfm_exact import PROJECT, STEP, _project, _sets, measured
from twin_core.api import InMemoryTwinAPI
from twin_core.consistency.record_pins import record_staleness, unregister_head_moved_listener
from twin_core.library.machines import SqliteMachineLibrary
from twin_core.manufacturing import (
    Authority,
    ContextField,
    ManufacturingContextRecord,
    normalize_orientation,
)


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


@pytest.fixture(autouse=True)
def publisher() -> Iterator[ops.OperationPublisher]:
    pub = ops.init_operations(None)
    yield pub
    ops.init_operations(None)


@pytest.fixture(autouse=True)
def listener() -> Iterator[None]:
    dfm.register()
    yield
    unregister_head_moved_listener(dfm.on_head_moved)


@pytest.fixture(autouse=True)
def machine_library() -> Iterator[SqliteMachineLibrary]:
    library = SqliteMachineLibrary()
    library_routes.init_machine_library(library)
    yield library
    library_routes.init_machine_library(None)


@pytest.fixture(autouse=True)
def _blobs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import digital_twin.storage.work_product_blobs as blobs

    monkeypatch.setattr(
        blobs,
        "store_work_product_blob",
        lambda node_id, filename, content, content_type="": f"wp/{node_id}/{filename}",
    )
    monkeypatch.setenv("ADAPTER_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.delenv(dfm_slicer.SLICER_CHECK_ENV, raising=False)


SLICED = {
    "gcode_path": "output/slicer/abc/part.gcode",
    "printer": "generic_220",
    "material": "PETG",
    "print_time_s": 5400,
    "print_time": "1h 30m 0s",
    "filament_cm3": 12.5,
    "filament_g": 15.9,
    "layers": 200,
    "height_mm": 40.0,
    "slicer": "PrusaSlicer-2.8.1",
    "messages": ["Detected print stability issues: low bed adhesion"],
}


class FakeAdapters:
    """The exact CAD service: the measurement and, when asked, the slicer."""

    def __init__(
        self,
        result: dict[str, Any] | Exception,
        slicer: dict[str, Any] | Exception | None = None,
    ) -> None:
        self.result = result
        self.slicer = slicer
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def read_step(self, wp: Any) -> bytes:
        return STEP

    async def invoke_tool(self, tool_id: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_id, dict(params)))
        if tool_id == "cadquery.dfm_measure":
            if isinstance(self.result, Exception):
                raise self.result
            return self.result
        assert tool_id == "slicer.slice"
        # The STEP is on the shared workspace while it is sliced.
        assert Path(params["file_path"]).read_bytes() == STEP
        if self.slicer is None or isinstance(self.slicer, Exception):
            raise self.slicer or RuntimeError("tool 'slicer.slice' is not served")
        if params.get("supports") is False:
            return {**self.slicer, "filament_cm3": 10.0, "messages": []}
        return self.slicer

    def directions(self) -> list[list[float]]:
        return [p["build_direction"] for t, p in self.calls if t == "cadquery.dfm_measure"]


def _orientation(value: Any, authority: Authority = Authority.USER_STATED) -> ContextField:
    return ContextField(
        value=value, authority=authority, provenance=mc.stated(1, source="api").provenance
    )


async def _exact(twin: Any, node_id: UUID, adapters: FakeAdapters, **kw: Any) -> dict[str, Any]:
    return await dfm_exact.run_exact(twin, PROJECT, node_id=str(node_id), service=adapters, **kw)


async def _exact_set(twin: Any, finding_set_id: str) -> Any:
    node = await twin.get_work_product(UUID(finding_set_id))
    return dfm.set_of(node), node


# ── the orientation field ────────────────────────────────────────────────


def test_an_orientation_is_a_unit_direction_with_an_optional_turn() -> None:
    assert normalize_orientation("+Z") == {"build_direction": [0.0, 0.0, 1.0], "rotation_deg": None}
    assert normalize_orientation("-y")["build_direction"] == [0.0, -1.0, 0.0]
    out = normalize_orientation({"build_direction": [0, 0, 5], "rotation_deg": 450})
    assert out == {"build_direction": [0.0, 0.0, 1.0], "rotation_deg": 90.0}
    for bad in ([0, 0, 0], [1, 2], "up", {"build_direction": [1, 0, 0], "rotation_deg": "x"}):
        with pytest.raises(ValueError):
            normalize_orientation(bad)


def test_an_orientation_is_stated_or_assumed_never_decided() -> None:
    record = ManufacturingContextRecord(
        project_id=PROJECT, print_orientation=_orientation([1, 0, 0])
    )
    assert record.print_orientation.value["build_direction"] == [1.0, 0.0, 0.0]
    assert "print_orientation" not in record.unknowns
    with pytest.raises(ValueError, match="never decided"):
        ManufacturingContextRecord(
            project_id=PROJECT, print_orientation=_orientation([1, 0, 0], Authority.DECISION)
        )


# ── orientation drives the exact checks ──────────────────────────────────


async def test_the_context_orientation_drives_the_exact_checks(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    _key, node_id = await _project(twin, machine_library)
    await mc.write_context(
        twin, PROJECT, {"print_orientation": mc.stated("+X", source="api")}, source="api"
    )
    adapters = FakeAdapters(measured())
    out = await _exact(twin, node_id, adapters, slicer_check=False)
    assert adapters.directions() == [[1.0, 0.0, 0.0]]
    fs, _node = await _exact_set(twin, out["finding_set_id"])
    source = fs.exact_evidence["build_direction_source"]
    assert source.startswith("manufacturing context MFC-PROJECT@")
    assert "stated by a person" in source

    # The request's direction still overrides the context, explicitly.
    adapters = FakeAdapters(measured())
    out = await _exact(twin, node_id, adapters, slicer_check=False, build_direction=[0, 1, 0])
    assert adapters.directions() == [[0.0, 1.0, 0.0]]
    fs, _node = await _exact_set(twin, out["finding_set_id"])
    assert fs.exact_evidence["build_direction_source"] == "stated in the request"


async def test_scheduling_reads_the_context_orientation(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    _key, node_id = await _project(twin, machine_library)
    await mc.write_context(
        twin,
        PROJECT,
        {
            "print_orientation": mc.stated(
                {"build_direction": [0, -1, 0], "rotation_deg": 45}, source="api"
            )
        },
        source="api",
    )

    class Runner:
        args: dict[str, Any] = {}

        async def start(self, tool: str, args: Any, work: Any, **kw: Any) -> Any:
            Runner.args = dict(args)
            return type("J", (), {"job_id": "job-1"})(), False

        def reply(self, job: Any, reused: bool) -> dict[str, Any]:
            return {"job_id": job.job_id}

    out = await dfm_exact.schedule(twin, PROJECT, node_id=str(node_id), job_runner=Runner())
    assert out["build_direction"] == [0.0, -1.0, 0.0]
    assert "manufacturing context" in out["build_direction_source"]
    assert Runner.args["build_direction_explicit"] is False
    # A retried job re-reads the context instead of pinning the old direction.
    assert (
        dfm_exact.exact_retry({"project_id": PROJECT, "node_id": str(node_id), **Runner.args})
        is not None
    )


async def test_changing_the_orientation_makes_the_exact_findings_stale(
    twin: InMemoryTwinAPI,
    machine_library: SqliteMachineLibrary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _key, node_id = await _project(twin, machine_library)
    first = await _exact(twin, node_id, FakeAdapters(measured()), slicer_check=False)
    assert first["status"] == "analysis_passed"
    followups: list[str] = []

    async def followup(twin: Any, pid: Any, gathered: Any, *, trigger: str = "") -> None:
        followups.append(gathered.component_id)

    monkeypatch.setattr(dfm_exact, "schedule_for_component", followup)
    await mc.write_context(
        twin, PROJECT, {"print_orientation": mc.stated("+Y", source="api")}, source="api"
    )
    _fs, node = await _exact_set(twin, first["finding_set_id"])
    assert record_staleness(node.metadata) in ("stale", "superseded")
    assert followups, "the exact checks were not scheduled again"
    # The re-run measures along the new direction.
    adapters = FakeAdapters(measured())
    await _exact(twin, node_id, adapters, slicer_check=False)
    assert adapters.directions() == [[0.0, 1.0, 0.0]]


async def test_a_component_orientation_overrides_the_product_context(
    twin: InMemoryTwinAPI,
    machine_library: SqliteMachineLibrary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _key, node_id = await _project(twin, machine_library)
    first = await _exact(twin, node_id, FakeAdapters(measured()), slicer_check=False)

    async def followup(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(dfm_exact, "schedule_for_component", followup)
    await mc.write_context(
        twin,
        PROJECT,
        {"print_orientation": mc.stated("-X", source="api")},
        source="api",
        component_key="Enclosure",
    )
    _fs, node = await _exact_set(twin, first["finding_set_id"])
    assert record_staleness(node.metadata) in ("stale", "superseded")
    adapters = FakeAdapters(measured())
    out = await _exact(twin, node_id, adapters, slicer_check=False)
    assert adapters.directions() == [[-1.0, 0.0, 0.0]]
    fs, _node = await _exact_set(twin, out["finding_set_id"])
    assert "MFC-ENCLOSURE@1" in fs.exact_evidence["build_direction_source"]


# ── the slicer cross-check ───────────────────────────────────────────────


async def test_slicer_evidence_is_recorded_on_the_exact_set(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary, tmp_path: Path
) -> None:
    _key, node_id = await _project(twin, machine_library)
    adapters = FakeAdapters(measured(), slicer=SLICED)
    out = await _exact(twin, node_id, adapters)
    assert out["status"] == "analysis_passed"
    slices = [p for t, p in adapters.calls if t == "slicer.slice"]
    assert [p["supports"] for p in slices] == [True, False]
    # Workshop Mini is 180 mm on each side: the smallest preset that holds it.
    assert slices[0]["printer"] == "generic_250" and slices[0]["material"] == "PETG"
    fs, node = await _exact_set(twin, out["finding_set_id"])
    slicer = fs.exact_evidence["slicer"]
    assert slicer["status"] == "evaluated" and slicer["affects_status"] is False
    assert slicer["print_time_s"] == 5400 and slicer["print_time_is_estimate"] is True
    assert "estimate" in slicer["estimate_note"]
    assert slicer["support_volume_cm3"] == 2.5
    assert slicer["warnings"] == SLICED["messages"]
    assert node.metadata["dfm_status"] == "analysis_passed"
    # The staged STEP is gone once sliced.
    assert not any((tmp_path / "_dfm_slicer").glob("*/part.step"))

    report = await dfm_report.build_report(twin, PROJECT)
    (comp,) = [c for c in report["components"] if c["dfm"]]
    assert comp["dfm"]["measurement"]["slicer"]["status"] == "evaluated"
    markdown = dfm_report.render_markdown(report)
    assert "Slicer cross-check" in markdown and "an estimate" in markdown


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (measured(wall_status="not_measured"), "analysis_pending"),
        (measured(wall=0.3), "issues_found"),
    ],
)
async def test_a_good_slice_never_passes_a_set_by_itself(
    twin: InMemoryTwinAPI,
    machine_library: SqliteMachineLibrary,
    result: dict[str, Any],
    expected: str,
) -> None:
    _key, node_id = await _project(twin, machine_library)
    out = await _exact(twin, node_id, FakeAdapters(result, slicer=SLICED))
    assert out["status"] == expected
    fs, _node = await _exact_set(twin, out["finding_set_id"])
    assert fs.exact_evidence["slicer"]["status"] == "evaluated"
    assert fs.status.value == expected


async def test_an_unavailable_slicer_is_not_evaluated_with_the_reason(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    _key, node_id = await _project(twin, machine_library)
    out = await _exact(twin, node_id, FakeAdapters(measured(), slicer=None))
    assert out["status"] == "analysis_passed"
    fs, _node = await _exact_set(twin, out["finding_set_id"])
    slicer = fs.exact_evidence["slicer"]
    assert slicer["status"] == "not_evaluated" and slicer["reason_code"] == "unavailable"
    assert "not served" in slicer["reason"]
    assert "print_time_s" not in slicer


async def test_a_failed_slice_is_not_evaluated(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    _key, node_id = await _project(twin, machine_library)
    adapters = FakeAdapters(measured(), slicer=ValueError("slicer.slice failed: empty layers"))
    out = await _exact(twin, node_id, adapters)
    fs, _node = await _exact_set(twin, out["finding_set_id"])
    assert fs.exact_evidence["slicer"]["reason_code"] == "failed"
    assert out["status"] == "analysis_passed"


async def test_no_machine_or_a_turned_part_is_not_sliced(
    twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    _key, node_id = await _project(twin, machine_library, machine=False)
    adapters = FakeAdapters(measured(), slicer=SLICED)
    out = await _exact(twin, node_id, adapters)
    fs, _node = await _exact_set(twin, out["finding_set_id"])
    assert fs.exact_evidence["slicer"]["reason_code"] == "no_machine"
    assert not [t for t, _p in adapters.calls if t == "slicer.slice"]

    other = InMemoryTwinAPI.create()
    _key, node_id = await _project(other, machine_library)
    adapters = FakeAdapters(measured(), slicer=SLICED)
    out = await _exact(other, node_id, adapters, build_direction=[1, 0, 0])
    fs, _node = await _exact_set(other, out["finding_set_id"])
    assert fs.exact_evidence["slicer"]["reason_code"] == "orientation"


async def test_the_cross_check_can_be_switched_off(
    twin: InMemoryTwinAPI,
    machine_library: SqliteMachineLibrary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _key, node_id = await _project(twin, machine_library)
    monkeypatch.setenv(dfm_slicer.SLICER_CHECK_ENV, "off")
    adapters = FakeAdapters(measured(), slicer=SLICED)
    out = await _exact(twin, node_id, adapters)
    fs, _node = await _exact_set(twin, out["finding_set_id"])
    assert fs.exact_evidence["slicer"]["reason_code"] == "disabled"
    assert not [t for t, _p in adapters.calls if t == "slicer.slice"]


async def test_no_workspace_is_unavailable(tmp_path: Path) -> None:
    from tests.unit.test_dfm_exact import _machine

    out = await dfm_slicer.cross_check(
        None,
        step=STEP,
        geometry_ref="CAD-X@1",
        machine=_machine(),
        material="PLA",
        build_direction=(0.0, 0.0, 1.0),
        workspace_dir=tmp_path / "missing",
    )
    assert out["status"] == "not_evaluated" and out["reason_code"] == "unavailable"


def test_the_presets_mirror_the_slicer_adapter() -> None:
    from tool_registry.tools.slicer.adapter import MATERIALS, PRINTERS

    assert {k: tuple(float(v) for v in p["size_mm"]) for k, p in PRINTERS.items()} == (
        dfm_slicer.SLICER_PRINTERS
    )
    assert set(MATERIALS) == dfm_slicer.SLICER_MATERIALS
    assert dfm_slicer.printer_for([250, 250, 250])[0] == "generic_256"
    assert dfm_slicer.printer_for([500, 500, 500])[0] == "generic_300"
    assert dfm_slicer.material_for("Nylon-CF")[0] is None


# ── the MCP tools ────────────────────────────────────────────────────────


@pytest.fixture
def server(twin: InMemoryTwinAPI) -> Any:
    from tool_registry.tools.twin.adapter import TwinServer

    return TwinServer(twin=twin, dfm_service=dfm.make_dfm_service(twin))


NEW_TOOLS = (
    "twin.get_manufacturing_context",
    "twin.state_manufacturing_context",
    "twin.get_dfm_report",
    "twin.get_release_checklist",
)


def test_the_tools_are_registered_and_classified(server: Any) -> None:
    assert set(NEW_TOOLS) <= set(server.tool_ids)
    for read in ("twin.get_manufacturing_context", "twin.get_dfm_report"):
        assert annotations_for(read)["readOnlyHint"] is True
    assert annotations_for("twin.get_release_checklist")["readOnlyHint"] is True
    write = annotations_for("twin.state_manufacturing_context")
    assert write["readOnlyHint"] is False and write["destructiveHint"] is False
    # Held like any twin write, never refused to a service, and the approver is recorded.
    assert decide("twin.state_manufacturing_context", caller=Caller.REMOTE).requires_approval
    assert not decide("twin.state_manufacturing_context", caller=Caller.SERVICE).refused
    assert records_approver("twin.state_manufacturing_context")
    assert not requires_human_authority("twin.state_manufacturing_context")
    # A client can never hand the tool its own approver.
    assert APPROVED_BY_ARG not in strip_reserved_arguments({APPROVED_BY_ARG: "me", "a": 1})


async def test_an_agent_proposes_and_reads_the_context(server: Any) -> None:
    empty = await server.get_manufacturing_context({"project_id": PROJECT})
    assert empty["ref"] == "unresolved" and empty["recorded"] is False

    out = await server.state_manufacturing_context(
        {
            "project_id": PROJECT,
            "fields": {"selected_process": "FDM", "print_orientation": "+Z"},
            "notes": {"selected_process": "a desk lamp shade, one-off"},
        }
    )
    assert out["statement"] == "proposal" and out["stated_by"] is None
    record = out["record"]
    assert record["selected_process"]["authority"] == "assumption"
    assert record["selected_process"]["provenance"]["source"] == "mcp_agent"
    assert record["print_orientation"]["process_specific"] is True
    read = await server.get_manufacturing_context({"project_id": PROJECT})
    assert read["ref"] == "MFC-PROJECT@1"


async def test_an_agent_cannot_make_a_statement(server: Any) -> None:
    with pytest.raises(ValueError, match="no person approved"):
        await server.state_manufacturing_context(
            {
                "project_id": PROJECT,
                "fields": {"selected_process": "FDM"},
                "statement": "user_stated",
            }
        )
    # A project policy's automatic approval is not a person either.
    with pytest.raises(ValueError, match="no person approved"):
        await server.state_manufacturing_context(
            {
                "project_id": PROJECT,
                "fields": {"selected_process": "FDM"},
                "statement": "user_stated",
                APPROVED_BY_ARG: "policy:auto:owner",
            }
        )
    with pytest.raises(ValueError, match="never assumed"):
        await server.state_manufacturing_context(
            {"project_id": PROJECT, "fields": {"target_cost": "20 GBP"}}
        )
    with pytest.raises(ValueError, match="person's statement"):
        await server.state_manufacturing_context(
            {"project_id": PROJECT, "fields": {"selected_material": None}}
        )
    with pytest.raises(ValueError, match="statement"):
        await server.state_manufacturing_context(
            {"project_id": PROJECT, "fields": {"selected_process": "FDM"}, "statement": "decision"}
        )


async def test_a_person_approved_call_is_user_stated_and_wins(server: Any) -> None:
    stated = await server.state_manufacturing_context(
        {
            "project_id": PROJECT,
            "fields": {"selected_process": "FDM", "target_cost": "20 GBP"},
            "statement": "user_stated",
            # What the dispatcher sets when a person approved the held call.
            APPROVED_BY_ARG: "local:dashboard",
        }
    )
    assert stated["stated_by"] == "local:dashboard"
    field = stated["record"]["selected_process"]
    assert field["authority"] == "user_stated"
    assert field["provenance"]["actor"] == "local:dashboard"
    assert stated["record"]["target_cost"]["authority"] == "user_stated"

    # An agent's later proposal never overrides it; the refusal is returned.
    proposed = await server.state_manufacturing_context(
        {"project_id": PROJECT, "fields": {"selected_process": "SLA"}}
    )
    assert proposed["record"]["selected_process"]["value"] == "FDM"
    assert [c["field"] for c in proposed["refused"]] == ["selected_process"]


async def test_the_report_and_checklist_tools_read(
    server: Any, twin: InMemoryTwinAPI, machine_library: SqliteMachineLibrary
) -> None:
    none = await server.get_release_checklist({"project_id": PROJECT})
    assert none["applies"] is False

    _key, node_id = await _project(twin, machine_library)
    await _exact(twin, node_id, FakeAdapters(measured()), slicer_check=False)
    checklist = await server.get_release_checklist({"project_id": PROJECT})
    assert checklist["applies"] is True and checklist["items"]

    preview = await server.get_dfm_report({"project_id": PROJECT})
    assert preview["stored"] is False and preview["report"]["components"]
    before = len(await _sets(twin))
    await dfm_report.generate_report(twin, PROJECT)
    stored = await server.get_dfm_report({"project_id": PROJECT})
    assert stored["stored"] is True and stored["report_id"]
    assert len(await _sets(twin)) == before  # reading wrote nothing

    with pytest.raises(ValueError, match="project_id"):
        await server.get_dfm_report({})


async def test_a_bad_orientation_is_refused_with_the_reason() -> None:
    with pytest.raises(tools.ManufacturingToolError, match="print_orientation"):
        tools.updates_from_tool({"print_orientation": [0, 0, 0]}, statement="proposal", person=None)
