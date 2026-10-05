"""Flow deliverable slots carry item keys (FORGE-524).

Each deliverable slot in a flow version gets an item key, frozen with the
version; during a run every definition write in a phase resolves to its
slot's item, so the model's naming cannot create a duplicate. A write that
matches no slot is still recorded, flagged as undeclared, and listed for the
gate reviewer.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from api_gateway.twin.constraint_recorder import make_constraint_recorder
from api_gateway.twin.geometry_recorder import make_geometry_recorder
from mcp_core.context import (
    HEADER_ITEM_SLOTS,
    ItemSlotClaim,
    McpCallContext,
    context_from_headers,
    context_to_headers,
    current_context,
    decode_item_slots,
    with_context,
)
from orchestrator.design_flow.frozen import freeze_flow
from orchestrator.design_flow.generator import (
    Operation,
    OperationKind,
    TailoringError,
    apply_operations,
    parse_caller_operations,
)
from orchestrator.design_flow.slots import (
    bind_slots,
    effective_slots,
    match_slot,
    slots_brief,
)
from orchestrator.design_flow.spec import (
    DeliverableSlot,
    FlowDefinition,
    definition_from_frozen,
    get_flow,
)
from orchestrator.design_flow.versions import (
    FlowVersionStore,
    diff_flows,
    get_version_store,
    reset_version_store,
)
from twin_core.api import InMemoryTwinAPI
from twin_core.items import find_item, item_history

PROJECT = "66666666-6666-6666-6666-666666666666"

BRACKETS = [
    {"type": "cad_model", "name": "left bracket"},
    {"type": "cad_model", "name": "right bracket"},
]


def _step(body: str) -> str:
    return base64.b64encode(f"ISO-10303-21;\n{body}\nENDSEC;\n".encode()).decode("ascii")


@pytest.fixture(autouse=True)
def _blob_store(monkeypatch: pytest.MonkeyPatch) -> None:
    import digital_twin.storage.work_product_blobs as blobs

    monkeypatch.setattr(
        blobs,
        "store_work_product_blob",
        lambda node_id, filename, content, content_type="": f"wp/{node_id}/{filename}",
    )


@pytest.fixture
def twin() -> InMemoryTwinAPI:
    return InMemoryTwinAPI.create()


class _Counter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def record_flow_item_slot(self, item_type: str, outcome: str) -> None:
        self.calls.append((item_type, outcome))


@pytest.fixture
def counter(monkeypatch: pytest.MonkeyPatch) -> _Counter:
    import api_gateway.twin.item_revisions as ir

    fake = _Counter()
    monkeypatch.setattr(ir, "_metrics", fake)
    return fake


def _tailored_with_brackets() -> FlowDefinition:
    flow, applied = apply_operations(
        get_flow("mech_v1"),
        [
            Operation(
                OperationKind.DECLARE_ITEMS,
                "design",
                "the shelf hangs on two brackets",
                value=BRACKETS,
            )
        ],
    )
    assert len(applied) == 1
    return flow


def _phase(flow: FlowDefinition, phase_id: str) -> Any:
    return next(p for p in flow.phases if p.id == phase_id)


def _claims(*slots: tuple[str, str, str]) -> tuple[ItemSlotClaim, ...]:
    return tuple(ItemSlotClaim(item_type=t, name=n, item_key=k) for t, n, k in slots)


def _ctx(run_id: str, *slots: tuple[str, str, str]) -> McpCallContext:
    return McpCallContext(run_id=run_id, phase="design", item_slots=_claims(*slots))


LEFT = ("cad_model", "left bracket", "CAD-LEFT-BRACKET")
RIGHT = ("cad_model", "right bracket", "CAD-RIGHT-BRACKET")


# ---------------------------------------------------------------------------
# Slots in the flow
# ---------------------------------------------------------------------------


class TestSlotsInTheFlow:
    def test_default_slots_cover_singleton_deliverables_only(self) -> None:
        flow = get_flow("hardware_v1")
        assert [s.item_key for s in effective_slots(_phase(flow, "requirements"))] == [
            "CS-REQUIREMENTS"
        ]
        assert [s.item_key for s in effective_slots(_phase(flow, "intent"))] == ["INT-INTENT"]
        # Parts and needs come in several per phase: no shared default slot.
        assert effective_slots(_phase(flow, "design")) == ()
        assert effective_slots(_phase(flow, "needs")) == ()

    def test_declaring_two_brackets_yields_one_slot_per_part(self) -> None:
        design = _phase(bind_slots(_tailored_with_brackets()), "design")
        assert [(s.item_type, s.name, s.item_key) for s in design.slots] == [LEFT, RIGHT]

    def test_bare_names_take_the_phases_only_definition_type(self) -> None:
        flow, _ = apply_operations(
            get_flow("mech_v1"),
            [Operation(OperationKind.DECLARE_ITEMS, "design", "two", value=["shelf", "cleat"])],
        )
        keys = [s.item_key for s in effective_slots(_phase(flow, "design"))]
        assert keys == ["CAD-SHELF", "CAD-CLEAT"]

    def test_caller_declare_items_is_checked_strictly(self) -> None:
        base = get_flow("mech_v1")
        ops = parse_caller_operations(
            [{"op": "declare_items", "phase": "design", "value": BRACKETS, "rationale": "two"}],
            base,
        )
        assert ops[0].kind is OperationKind.DECLARE_ITEMS
        with pytest.raises(TailoringError, match="declare_items"):
            parse_caller_operations(
                [{"op": "declare_items", "phase": "design", "value": [], "rationale": "x"}],
                base,
            )
        with pytest.raises(TailoringError, match="declare_items"):
            # A bare name on a phase producing no definition type has no type.
            parse_caller_operations(
                [{"op": "declare_items", "phase": "simulation", "value": ["x"], "rationale": "x"}],
                base,
            )

    def test_bind_is_idempotent(self) -> None:
        once = bind_slots(_tailored_with_brackets())
        assert bind_slots(once) == once

    def test_slots_are_frozen_into_the_hash_and_round_trip(self) -> None:
        bound = bind_slots(_tailored_with_brackets())
        frozen = freeze_flow(bound)
        assert [s.item_key for s in frozen.phases[4].slots] == [LEFT[2], RIGHT[2]]
        assert freeze_flow(definition_from_frozen(frozen)).content_hash == frozen.content_hash
        renamed = freeze_flow(
            FlowDefinition(
                id=bound.id,
                name=bound.name,
                phases=tuple(
                    p
                    if p.id != "design"
                    else type(p)(**{**p.__dict__, "slots": (DeliverableSlot(*LEFT),)})
                    for p in bound.phases
                ),
            )
        )
        assert renamed.content_hash != frozen.content_hash

    def test_a_flow_without_slots_keeps_its_pre_slot_hash(self) -> None:
        # The hash rows omit an empty slots list, as they omit an unset model.
        frozen = freeze_flow(get_flow("mech_v1"))
        assert all(p.slots == [] for p in frozen.phases)
        import hashlib
        import json
        from dataclasses import asdict

        rows = []
        for p in frozen.phases:
            row = asdict(p)
            row.pop("slots")
            if row.get("model") is None:
                row.pop("model")
            rows.append(row)
        legacy = hashlib.sha256(
            json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert frozen.content_hash == legacy

    def test_keys_are_stable_across_versions(self, tmp_path) -> None:
        path = str(tmp_path / "versions.db")
        store = FlowVersionStore(path)
        first = store.save(
            _tailored_with_brackets(), base_template_id="mech_v1", base_version="1", changes=["a"]
        )
        second = store.save(
            _tailored_with_brackets(), base_template_id="mech_v1", base_version="1", changes=["b"]
        )

        def keys(v: Any) -> list[str]:
            return [s.item_key for p in v.frozen.phases for s in p.slots]

        assert keys(first) == keys(second)
        assert "CAD-LEFT-BRACKET" in keys(first) and "CS-REQUIREMENTS" not in keys(first)
        # Survives a restart, hash and all.
        restored = FlowVersionStore(path).get(first.id)
        assert keys(restored) == keys(first)
        assert restored.frozen.content_hash == first.frozen.content_hash

    def test_default_slots_are_derived_not_stored(self) -> None:
        # A flow that declares nothing hashes exactly as before slots existed.
        base = get_flow("hardware_v1")
        assert all(p.slots == () for p in bind_slots(base).phases)
        version = FlowVersionStore().save(
            base, base_template_id="hardware_v1", base_version="1", changes=["x"]
        )
        assert all(p.slots == [] for p in version.frozen.phases)
        assert (
            version.frozen.content_hash
            == freeze_flow(base, version=version.frozen.version).content_hash
        )
        # ...and still gets its default slots at run time.
        requirements = _phase(definition_from_frozen(version.frozen), "requirements")
        assert [s.item_key for s in effective_slots(requirements)] == ["CS-REQUIREMENTS"]

    def test_a_stored_pre_change_version_still_verifies(self, tmp_path) -> None:
        import json
        from dataclasses import asdict

        path = str(tmp_path / "versions.db")
        store = FlowVersionStore(path)  # creates the table
        flow = get_flow("mech_v1")
        definition = asdict(flow)
        for phase in definition["phases"]:
            phase.pop("slots")  # the row shape written before FORGE-524
        frozen = freeze_flow(flow, version="1+flowv_old")
        assert store._conn is not None
        store._conn.execute(
            "INSERT INTO flow_versions (id, status, definition, frozen_version, content_hash, "
            "base_template_id, base_version, changes, origin, intent, created_at, decided_by, "
            "decided_at, approval_id, flow_context) VALUES "
            "(?, 'approved', ?, ?, ?, 'mech_v1', '1', '[]', 'edited', '', ?, 'user:x', '', '', '')",
            (
                "flowv_old",
                json.dumps(definition, sort_keys=True),
                frozen.version,
                frozen.content_hash,
                datetime.now(UTC).isoformat(),
            ),
        )
        store._conn.commit()

        restored = FlowVersionStore(path).get("flowv_old")
        restored.frozen.verify()
        assert restored.frozen.content_hash == frozen.content_hash
        assert restored.startable

    def test_diff_names_declared_items_but_not_materialised_defaults(self) -> None:
        base = get_flow("hardware_v1")
        assert diff_flows(base, bind_slots(base)) == []
        lines = diff_flows(get_flow("mech_v1"), bind_slots(_tailored_with_brackets()))
        assert "phase 'design' declares cad_model 'left bracket' (item CAD-LEFT-BRACKET)" in lines

    def test_brief_lists_the_keys(self) -> None:
        brief = slots_brief(effective_slots(_phase(_tailored_with_brackets(), "design")))
        assert "CAD-LEFT-BRACKET: cad_model 'left bracket'" in brief
        assert "undeclared" in brief
        assert slots_brief(()) == ""


class TestMatchSlot:
    slots = _claims(LEFT, RIGHT)

    @pytest.mark.parametrize(
        ("name", "key", "how"),
        [
            ("Left Bracket", "CAD-LEFT-BRACKET", "name"),
            ("left-bracket", "CAD-LEFT-BRACKET", "name"),
            ("Bracket, left side v2", "CAD-LEFT-BRACKET", "fuzzy"),
            ("RH bracket (right)", "CAD-RIGHT-BRACKET", "fuzzy"),
            ("Mounting plate", None, "ambiguous"),
            ("bracket", None, "ambiguous"),
        ],
    )
    def test_two_slots(self, name: str, key: str | None, how: str) -> None:
        match = match_slot(self.slots, "cad_model", name)
        assert (match.slot.item_key if match.slot else None, match.how) == (key, how)

    def test_one_slot_takes_any_name(self) -> None:
        match = match_slot(_claims(LEFT), "cad_model", "Wall hanger")
        assert (match.slot.item_key, match.how) == ("CAD-LEFT-BRACKET", "only")

    def test_other_type_matches_nothing(self) -> None:
        assert match_slot(self.slots, "constraint_set", "left bracket").how == "none"


class TestContextCarriesSlots:
    def test_headers_round_trip(self) -> None:
        ctx = _ctx("run-1", LEFT, RIGHT)
        back = context_from_headers(context_to_headers(ctx))
        assert back.item_slots == ctx.item_slots
        assert back.run_id == "run-1"

    @pytest.mark.parametrize("raw", ["not json", "{}", '[["a","b"]]', "x" * 9000, "[[1,2,3]]"])
    def test_malformed_header_is_no_slots(self, raw: str) -> None:
        assert decode_item_slots(raw) == ()
        assert context_from_headers({HEADER_ITEM_SLOTS: raw}).item_slots == ()

    def test_phase_scope_adds_slots_to_the_run_context(self) -> None:
        from api_gateway.runs.flow_brain import phase_slot_scope

        slots = (DeliverableSlot(*LEFT),)
        run_ctx = McpCallContext(run_id="run-9", phase="design")
        with with_context(run_ctx), phase_slot_scope(slots, PROJECT):
            inner = current_context()
            assert inner.run_id == "run-9"
            assert inner.item_slots == _claims(LEFT)
        # No context installed (in-process engine): one is created for the project.
        with phase_slot_scope(slots, PROJECT):
            assert str(current_context().project_id) == PROJECT
            assert current_context().item_slots == _claims(LEFT)
        # No slots: nothing changes.
        with with_context(run_ctx), phase_slot_scope((), PROJECT):
            assert current_context() is run_ctx

    def test_worker_phase_keeps_the_frozen_slots(self) -> None:
        from api_gateway.runs.flow_worker import _slots_of

        frozen = freeze_flow(bind_slots(_tailored_with_brackets()))
        assert [s.item_key for s in _slots_of(frozen.phases[4])] == [LEFT[2], RIGHT[2]]
        as_dict = {"slots": [{"item_type": "cad_model", "name": "x", "item_key": "CAD-X"}]}
        assert _slots_of(SimpleNamespace(**as_dict))[0].item_key == "CAD-X"


# ---------------------------------------------------------------------------
# Writes resolve to slots
# ---------------------------------------------------------------------------


class TestWritesResolveToSlots:
    async def test_drifted_name_lands_on_the_slots_item(self, twin, counter) -> None:
        record = make_geometry_recorder(twin, None)
        with with_context(_ctx("run-1", LEFT, RIGHT)):
            v1 = await record(step_base64=_step("a"), name="Left Bracket", project_id=PROJECT)
        with with_context(_ctx("run-2", LEFT, RIGHT)):
            v2 = await record(
                step_base64=_step("b"), name="Bracket, left side v2", project_id=PROJECT
            )

        assert (v1["item_key"], v1["revision"]) == ("CAD-LEFT-BRACKET", 1)
        assert (v2["item_key"], v2["revision"]) == ("CAD-LEFT-BRACKET", 2)
        assert v2["flow_slot_key"] == "CAD-LEFT-BRACKET"
        assert "undeclared_item" not in v2
        item = await find_item(twin, "CAD-LEFT-BRACKET", PROJECT)
        assert [r.run_id for r in await item_history(twin, item)] == ["run-1", "run-2"]
        wp = await twin.get_work_product(UUID(v2["node_id"]))
        assert wp.metadata["flow_slot_key"] == "CAD-LEFT-BRACKET"
        assert counter.calls == [("cad_model", "slot"), ("cad_model", "slot")]

    async def test_one_slot_absorbs_a_rename_from_a_later_run(self, twin) -> None:
        record = make_geometry_recorder(twin, None)
        with with_context(_ctx("run-1", LEFT)):
            await record(step_base64=_step("a"), name="Left Bracket", project_id=PROJECT)
        with with_context(_ctx("run-2", LEFT)):
            v2 = await record(step_base64=_step("b"), name="Wall hanger", project_id=PROJECT)
        assert (v2["item_key"], v2["revision"]) == ("CAD-LEFT-BRACKET", 2)

    async def test_a_second_name_in_the_same_run_is_a_new_part(self, twin, counter) -> None:
        record = make_geometry_recorder(twin, None)
        with with_context(_ctx("run-1", LEFT)):
            await record(step_base64=_step("a"), name="Left Bracket", project_id=PROJECT)
            again = await record(step_base64=_step("b"), name="Left Bracket", project_id=PROJECT)
            other = await record(step_base64=_step("c"), name="Shelf board", project_id=PROJECT)
        assert (again["item_key"], again["revision"]) == ("CAD-LEFT-BRACKET", 2)
        assert other["item_key"] == "CAD-SHELF-BOARD"
        assert other["undeclared_item"] is True
        assert counter.calls[-1] == ("cad_model", "undeclared")

    async def test_undeclared_write_is_recorded_and_flagged(self, twin, counter) -> None:
        record = make_geometry_recorder(twin, None)
        with with_context(_ctx("run-1", LEFT, RIGHT)):
            out = await record(step_base64=_step("a"), name="Mounting plate", project_id=PROJECT)

        assert out["item_key"] == "CAD-MOUNTING-PLATE"
        assert out["revision"] == 1
        assert out["undeclared_item"] is True
        assert "CAD-LEFT-BRACKET" in out["undeclared_item_note"]
        wp = await twin.get_work_product(UUID(out["node_id"]))
        assert wp.metadata["undeclared_item"] is True
        assert wp.metadata["undeclared_phase"] == "design"
        assert wp.metadata["declared_item_keys"] == ["CAD-LEFT-BRACKET", "CAD-RIGHT-BRACKET"]
        assert counter.calls == [("cad_model", "undeclared")]

    async def test_explicit_item_key_wins_and_is_judged_against_the_slots(self, twin) -> None:
        record = make_geometry_recorder(twin, None)
        with with_context(_ctx("run-1", LEFT, RIGHT)):
            right = await record(
                step_base64=_step("a"),
                name="left bracket",
                project_id=PROJECT,
                item_key="CAD-RIGHT-BRACKET",
            )
            stray = await record(
                step_base64=_step("b"), name="x", project_id=PROJECT, item_key="CAD-SPARE"
            )
        assert right["item_key"] == "CAD-RIGHT-BRACKET"
        assert "undeclared_item" not in right
        assert stray["item_key"] == "CAD-SPARE"
        assert stray["undeclared_item"] is True

    async def test_types_the_phase_declares_nothing_for_are_untouched(self, twin) -> None:
        record = make_constraint_recorder(twin, None)
        c = [{"name": "load", "expression": "True"}]
        with with_context(_ctx("run-1", LEFT)):
            out = await record(title="Bracket loads", constraints=c, project_id=PROJECT)
        assert out["item_key"] == "CS-BRACKET-LOADS"
        assert "undeclared_item" not in out
        assert "flow_slot_key" not in out

    async def test_requirements_default_slot_absorbs_title_drift(self, twin) -> None:
        record = make_constraint_recorder(twin, None)
        c = [{"name": "load", "expression": "True"}]
        slot = ("constraint_set", "requirements", "CS-REQUIREMENTS")
        with with_context(_ctx("run-1", slot)):
            v1 = await record(title="Shelf requirements", constraints=c, project_id=PROJECT)
        with with_context(_ctx("run-2", slot)):
            v2 = await record(title="Requirements (rev B)", constraints=c, project_id=PROJECT)
        assert (v1["item_key"], v1["revision"]) == ("CS-REQUIREMENTS", 1)
        assert (v2["item_key"], v2["revision"]) == ("CS-REQUIREMENTS", 2)

    async def test_no_slots_is_forge_523_behaviour(self, twin) -> None:
        record = make_geometry_recorder(twin, None)
        with with_context(McpCallContext(run_id="run-1")):
            v1 = await record(step_base64=_step("a"), name="Shelf Bracket", project_id=PROJECT)
            v2 = await record(step_base64=_step("b"), name="Wall Bracket", project_id=PROJECT)
        assert v1["item_key"] != v2["item_key"]
        assert "undeclared_item" not in v2


# ---------------------------------------------------------------------------
# The gate lists undeclared items
# ---------------------------------------------------------------------------


class TestGateListsUndeclaredItems:
    async def test_undeclared_items_are_findings_not_violations(self, twin) -> None:
        from api_gateway.runs.gate_eval import TwinConstraintChecker
        from orchestrator.design_flow.executor import (
            ConstraintReport,
            _constraint_details,
        )

        record = make_geometry_recorder(twin, None)
        with with_context(_ctx("run-1", LEFT, RIGHT)):
            ok = await record(step_base64=_step("a"), name="Left Bracket", project_id=PROJECT)
            odd = await record(step_base64=_step("b"), name="Mounting plate", project_id=PROJECT)

        now = datetime.now(UTC)
        wps = [
            SimpleNamespace(id=ok["node_id"], name="Left Bracket", updated_at=now),
            SimpleNamespace(id=odd["node_id"], name="Mounting plate", updated_at=now),
        ]

        class _Backend:
            async def get_project(self, _pid: str) -> Any:
                return SimpleNamespace(work_products=wps)

        checker = TwinConstraintChecker(twin, _Backend())
        findings = await checker._undeclared_items(PROJECT, 0.0)
        assert len(findings) == 1
        assert findings[0].startswith("undeclared item CAD-MOUNTING-PLATE")
        assert "CAD-LEFT-BRACKET, CAD-RIGHT-BRACKET" in findings[0]

        report = ConstraintReport(checked=True, undeclared_items=findings)
        assert report.passed
        assert "Undeclared items (1): undeclared item CAD-MOUNTING-PLATE" in _constraint_details(
            report
        )


# ---------------------------------------------------------------------------
# The version route exposes the slots
# ---------------------------------------------------------------------------


class TestVersionRouteShowsSlots:
    @pytest.fixture(autouse=True)
    def _fresh_store(self) -> Any:
        reset_version_store()
        yield
        reset_version_store()

    def test_get_version_lists_slots_and_keys(self) -> None:
        from api_gateway.server import create_app

        version = get_version_store().save(
            _tailored_with_brackets(), base_template_id="mech_v1", base_version="1", changes=["x"]
        )
        client = TestClient(create_app())
        body = client.get(f"/v1/design-flows/versions/{version.id}").json()
        phases = {p["id"]: p for p in body["flow"]["phases"]}
        assert phases["design"]["slots"] == [
            {
                "itemType": "cad_model",
                "name": "left bracket",
                "itemKey": "CAD-LEFT-BRACKET",
                "derived": False,
            },
            {
                "itemType": "cad_model",
                "name": "right bracket",
                "itemKey": "CAD-RIGHT-BRACKET",
                "derived": False,
            },
        ]
        # Default slots are shown, marked derived: computed, not stored.
        assert phases["intent"]["slots"] == [
            {"itemType": "intent", "name": "intent", "itemKey": "INT-INTENT", "derived": True}
        ]

    def test_edited_version_can_declare_slots(self) -> None:
        from api_gateway.server import create_app

        client = TestClient(create_app())
        flow = client.get("/v1/design-flows/mech_v1").json()
        phases = flow["phases"]
        for p in phases:
            if p["id"] == "design":
                p["slots"] = [{"itemType": "cad_model", "name": "cleat"}]
        resp = client.post(
            "/v1/design-flows/versions", json={"baseTemplateId": "mech_v1", "phases": phases}
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "phase 'design' declares cad_model 'cleat' (item CAD-CLEAT)" in body["changes"]
        design = next(p for p in body["flow"]["phases"] if p["id"] == "design")
        assert design["slots"] == [
            {"itemType": "cad_model", "name": "cleat", "itemKey": "CAD-CLEAT", "derived": False}
        ]
        # The derived slots the editor echoed back were not stored as declared.
        stored = get_version_store().get(body["versionId"])
        assert [s.item_key for p in stored.frozen.phases for s in p.slots] == ["CAD-CLEAT"]
