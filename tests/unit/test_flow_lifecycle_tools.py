"""The lifecycle MCP tools, on both binding paths (FORGE-539).

``flow.compile_intent``, ``flow.capabilities``, ``flow.lifecycle`` and
``flow.verify_completion`` must answer the same whether the MCP server runs
inside the gateway (in-process bindings) or as a sidecar calling the
gateway over HTTP (remote bindings). They are also read-only: classified so
an agent can call them without a human being asked.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from metaforge.mcp import remote_flows
from tests.unit.test_sidecar_flow_tools import (  # noqa: F401 - fixtures
    _MCP_CONTEXT,
    GATEWAY,
    _boot,
    _listed,
    gateway,
    sidecar_env,
)

LIFECYCLE_TOOLS = {
    "flow.compile_intent",
    "flow.capabilities",
    "flow.lifecycle",
    "flow.verify_completion",
}
#: The capped core profile carries these; flow.verify_completion's answer is
#: part of flow.lifecycle, so it is served only on the full set.
CORE_LIFECYCLE_TOOLS = {"flow.compile_intent", "flow.capabilities", "flow.lifecycle", "flow.patch"}

pytestmark = pytest.mark.asyncio


def _strip_ids(result: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result.items() if k != "run_id"}


class TestRegistered:
    @pytest.mark.usefixtures("sidecar_env")
    async def test_the_sidecar_lists_them_and_core_serves_them(self) -> None:
        assert LIFECYCLE_TOOLS | {"flow.patch"} <= await _listed(await _boot())
        assert CORE_LIFECYCLE_TOOLS <= await _listed(await _boot("--profile", "core"))

    def test_flow_patch_writes_but_is_approved_downstream(self) -> None:
        from mcp_core.annotations import annotations_for
        from mcp_core.guardrails import DOWNSTREAM_APPROVED

        annotations = annotations_for("flow.patch")
        assert annotations["readOnlyHint"] is False
        assert annotations["destructiveHint"] is False
        assert "flow.patch" in DOWNSTREAM_APPROVED

    def test_they_are_read_only(self) -> None:
        from mcp_core.annotations import annotations_for

        for tool in LIFECYCLE_TOOLS:
            annotations = annotations_for(tool)
            assert annotations["readOnlyHint"] is True, tool
            assert annotations["destructiveHint"] is False, tool


class TestParity:
    async def test_compile_intent(self, gateway: httpx.AsyncClient) -> None:
        from api_gateway.design_flows.mcp_bindings import make_intent_compiler

        remote = remote_flows.build_remote_flow_bindings(GATEWAY, client=gateway)
        args = {"intent": "a shelf that holds at least 40 kg", **_MCP_CONTEXT}
        over_http = await remote.intent_compiler(**args)
        assert over_http == await make_intent_compiler()(**args)
        assert over_http["intent"]["success_criteria"][0]["limit"] == 40.0

    async def test_capabilities(self, gateway: httpx.AsyncClient) -> None:
        from api_gateway.design_flows.mcp_bindings import make_capability_reader

        remote = remote_flows.build_remote_flow_bindings(GATEWAY, client=gateway)
        over_http = await remote.capability_reader(template="mech_v1")
        assert over_http == await make_capability_reader()(template="mech_v1")
        assert over_http["flow_id"] == "mech_v1"

    async def test_capabilities_needs_exactly_one_target(self, gateway: httpx.AsyncClient) -> None:
        from api_gateway.design_flows.mcp_bindings import make_capability_reader

        remote = remote_flows.build_remote_flow_bindings(GATEWAY, client=gateway)
        for reader in (remote.capability_reader, make_capability_reader()):
            with pytest.raises(RuntimeError, match="exactly one"):
                await reader()

    async def test_lifecycle(self, gateway: httpx.AsyncClient) -> None:
        import api_gateway.runs.routes as run_routes
        from api_gateway.design_flows.mcp_bindings import make_lifecycle_reader

        run = run_routes._store.create(
            {"kind": "design_flow", "flow": "mech_v1", "flow_engine": "in_process"}
        )
        run_routes._store.start(run.id)
        run_routes._store.fail(run.id, "Phase 'design' failed: boom")
        remote = remote_flows.build_remote_flow_bindings(GATEWAY, client=gateway)
        over_http = await remote.lifecycle_reader(run.id)
        local = await make_lifecycle_reader()(run.id)
        assert _strip_ids(over_http) == _strip_ids(local)
        assert over_http["completion"]["classification"] == "FAILED"
        assert over_http["next_step"].startswith("The run failed")


class TestVerifyCompletionTool:
    async def test_it_reports_only_the_verdict(self) -> None:
        from tool_registry.tools.design_flow.adapter import DesignFlowServer

        async def lifecycle(run_id: str) -> dict[str, Any]:
            return {
                "completion": {"classification": "PARTIALLY_COMPLETED", "verified": False},
                "requirements": [{"id": "R1", "status": "FAIL"}],
                "stale_item_keys": [],
                "limits": [],
                "nodes": ["not part of the verdict"],
                "next_step": "do not report it as done",
            }

        server = DesignFlowServer(lifecycle_reader=lifecycle)
        result = await server.verify_completion({"run_id": "r1"})
        assert result["completion"]["classification"] == "PARTIALLY_COMPLETED"
        assert "nodes" not in result
        assert result["next_step"] == "do not report it as done"
        with pytest.raises(ValueError, match="run_id"):
            await server.verify_completion({})


class TestPatchTool:
    async def test_arguments_are_checked_before_anything_is_called(self) -> None:
        from tool_registry.tools.design_flow.adapter import DesignFlowServer

        calls: list[dict[str, Any]] = []

        async def patcher(**kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {"status": "proposed"}

        server = DesignFlowServer(patcher=patcher)
        for bad, match in [
            ({"action": "undo", "run_id": "r"}, "action"),
            ({"action": "propose"}, "run_id"),
            ({"action": "propose", "run_id": "r", "reason": "x"}, "expected_content_hash"),
            ({"action": "propose", "run_id": "r", "expected_content_hash": "h"}, "reason"),
            ({"action": "apply", "run_id": "r"}, "version_id"),
        ]:
            with pytest.raises(ValueError, match=match):
                await server.patch(bad)
        assert calls == []
        await server.patch(
            {
                "action": "propose",
                "run_id": "r",
                "expected_content_hash": "h",
                "reason": "x",
                "invalidate": ["design"],
            }
        )
        assert calls[0]["invalidate"] == ["design"]
