"""`resources/list` has to list something a client can read (FORGE-408).

FORGE-337 moved MetaForge's parameterised resources out of `resources/list`
and into `resources/templates/list`, because they were being returned with a
`uri_template` key — neither the method nor the field the spec defines. That
was correct, and it left `resources/list` permanently empty with a comment
saying an empty list was "the honest answer".

It was honest and useless. A client that calls `resources/list` — which is
most of them, and was the agent in the FORGE-408 transcript — saw nothing and
concluded MetaForge publishes no context at all. The brief it needed was
reachable only by reading the templates list and assembling the URI by hand.

A template stops being a template once its parameter is known. Every
MetaForge resource is keyed by project, and the session carries a project
(FORGE-335), so with one in scope these *are* concrete.

The rest of FORGE-408 did not reproduce on current `main`: `project.open`
returns a brief and the brief resource reads. Those symptoms came from the
stale sidecar image on fidel-dev (FORGE-411). Tests for them are here anyway,
so the next stale deployment is distinguishable from a regression.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mcp_core.context import McpCallContext, with_context
from mcp_core.guardrails import ApprovalOutcome, Caller


async def _server():
    from api_gateway.projects.backend import InMemoryProjectBackend
    from api_gateway.projects.brief_provider import make_brief_provider
    from metaforge.mcp.server import build_unified_server
    from twin_core.api import InMemoryTwinAPI

    twin = InMemoryTwinAPI.create()
    backend = InMemoryProjectBackend.create()
    project = await backend.create_project(name="Probe Arm", description="d", status="draft")

    async def approve(ask: Any) -> ApprovalOutcome:
        return ApprovalOutcome.APPROVED

    server = await build_unified_server(
        adapter_ids=["project", "twin"],
        twin=twin,
        project_backend=backend,
        brief_provider=make_brief_provider(twin, backend),
        caller=Caller.LOCAL,
        approval_gate=approve,
    )
    return server, project


async def _call(server: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
    raw = await server.handle_request(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    )
    return json.loads(raw)


@pytest.mark.asyncio
class TestWithAProjectInScope:
    async def test_an_explicit_project_id_yields_concrete_resources(self) -> None:
        server, project = await _server()
        result = (await _call(server, "resources/list", {"project_id": project.id}))["result"]

        uris = [r["uri"] for r in result["resources"]]
        assert uris, "resources/list is still empty with a project named"
        assert f"metaforge://twin/brief/{project.id}" in uris
        assert result["_meta"]["project"] == project.id
        # No half-expanded entries: a URI with a placeholder left in it is a
        # URI a model will read and the server will reject.
        assert all("{" not in u for u in uris)

    async def test_the_session_scope_is_enough(self) -> None:
        """The normal case since FORGE-335: `project.open` sets the scope once
        and later calls carry no project at all."""
        server, project = await _server()
        ctx = McpCallContext(project_id=project.id)
        with with_context(ctx):
            result = (await _call(server, "resources/list", {}))["result"]
        assert len(result["resources"]) >= 1
        assert result["_meta"]["project"] == project.id

    async def test_every_listed_resource_actually_reads(self) -> None:
        """The assertion that matters. A list of URIs the server then refuses
        is worse than an empty list, because the model spends turns on it."""
        server, project = await _server()
        listed = (await _call(server, "resources/list", {"project_id": project.id}))["result"]
        for entry in listed["resources"]:
            read = await _call(server, "resources/read", {"uri": entry["uri"]})
            assert "error" not in read, f"{entry['uri']} listed but not readable: {read}"


@pytest.mark.asyncio
class TestWithoutAProject:
    async def test_the_list_is_empty_but_says_why(self) -> None:
        """ "No project is open" and "this server has no resources" look
        identical in an empty list and mean entirely different things."""
        server, _ = await _server()
        result = (await _call(server, "resources/list", {}))["result"]
        assert result["resources"] == []
        hint = result["_meta"]["hint"]
        assert "No project is in scope" in hint
        # The hint has to name the way out, not just the problem.
        assert "project.open" in hint
        assert "resources/templates/list" in hint

    async def test_the_templates_are_still_listed_where_the_spec_puts_them(self) -> None:
        # FORGE-337's rule holds: parameterised entries belong in the
        # templates list, and this change must not duplicate them into
        # `resources/list` unresolved.
        server, _ = await _server()
        result = (await _call(server, "resources/templates/list", {}))["result"]
        templates = [t["uriTemplate"] for t in result["resourceTemplates"]]
        assert any("{project_id}" in t for t in templates)


class TestOnlyTheProjectPlaceholderIsFilled:
    def test_a_template_with_another_parameter_is_left_alone(self) -> None:
        """Guessing a value for an unknown parameter publishes a URI that does
        not resolve — worse than not listing it, because a model will read
        it."""
        from metaforge.mcp.server import UnifiedMcpServer

        expand = UnifiedMcpServer._expand_templates_for_project
        entries = [
            {"uri_template": "metaforge://twin/brief/{project_id}", "name": "brief"},
            {"uri_template": "metaforge://twin/node/{project_id}/{node_id}", "name": "node"},
            {"uri_template": "metaforge://other/{thing}", "name": "other"},
        ]
        out = expand(None, entries, "p1")  # type: ignore[arg-type]
        assert [e["uri"] for e in out] == ["metaforge://twin/brief/p1"]

    def test_nothing_is_expanded_without_a_project(self) -> None:
        from metaforge.mcp.server import UnifiedMcpServer

        expand = UnifiedMcpServer._expand_templates_for_project
        entries = [{"uri_template": "metaforge://twin/brief/{project_id}"}]
        assert expand(None, entries, None) == []  # type: ignore[arg-type]

    def test_the_template_key_does_not_leak_into_the_concrete_entry(self) -> None:
        from metaforge.mcp.server import UnifiedMcpServer

        expand = UnifiedMcpServer._expand_templates_for_project
        out = expand(  # type: ignore[arg-type]
            None,
            [{"uri_template": "metaforge://twin/brief/{project_id}", "name": "brief"}],
            "p1",
        )
        assert "uri_template" not in out[0]
        assert out[0]["name"] == "brief"


@pytest.mark.asyncio
class TestTheStaleImageSymptoms:
    """Did not reproduce on current `main` — recorded so a real regression is
    distinguishable from the next stale deployment (FORGE-411)."""

    async def test_project_open_returns_a_brief(self) -> None:
        server, _ = await _server()
        response = await _call(
            server, "tools/call", {"name": "project.open", "arguments": {"query": "Probe Arm"}}
        )
        data = json.loads(response["result"]["content"][0]["text"])["data"]
        assert data["brief"], "project.open returned no brief"

    async def test_the_brief_resource_reads(self) -> None:
        server, project = await _server()
        read = await _call(
            server, "resources/read", {"uri": f"metaforge://twin/brief/{project.id}"}
        )
        assert "error" not in read
        assert read["result"]["contents"][0]["text"]
