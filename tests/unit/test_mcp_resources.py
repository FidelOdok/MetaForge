"""Unit tests for MCP resources scaffolding (MET-384)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from mcp_core.resources import (
    SCHEME,
    ParsedResourceUri,
    ResourceUriError,
    parse_resource_uri,
)
from mcp_core.schemas import (
    ResourceContent,
    ResourceListResult,
    ResourceManifest,
    ResourceReadResult,
)
from tool_registry.mcp_server.handlers import (
    ResourceManifestEntry,
)
from tool_registry.mcp_server.server import McpToolServer

# ---------------------------------------------------------------------------
# URI parsing
# ---------------------------------------------------------------------------


class TestParseResourceUri:
    @pytest.mark.parametrize(
        "uri,adapter,path",
        [
            ("metaforge://twin/node/abc", "twin", "node/abc"),
            ("metaforge://knowledge/doc/some-path.md", "knowledge", "doc/some-path.md"),
            ("metaforge://constraint/violation/uuid-123", "constraint", "violation/uuid-123"),
        ],
    )
    def test_valid_uri(self, uri: str, adapter: str, path: str) -> None:
        parsed = parse_resource_uri(uri)
        assert parsed.scheme == SCHEME
        assert parsed.adapter == adapter
        assert parsed.path == path
        # round-trip
        assert parsed.raw == uri

    @pytest.mark.parametrize(
        "uri",
        [
            "",
            "not a uri",
            "metaforge:///no-adapter",
            "metaforge://twin",  # missing trailing /
            "metaforge://",
        ],
    )
    def test_malformed(self, uri: str) -> None:
        with pytest.raises(ResourceUriError):
            parse_resource_uri(uri)

    def test_unknown_scheme(self) -> None:
        with pytest.raises(ResourceUriError, match="Unknown URI scheme"):
            parse_resource_uri("http://example.com/x")

    def test_explicit_scheme_allowlist(self) -> None:
        parsed = parse_resource_uri("file://twin/node/abc", allowed_schemes=("file",))
        assert parsed.scheme == "file"
        assert parsed.adapter == "twin"

    def test_returns_parsed_dataclass(self) -> None:
        parsed = parse_resource_uri("metaforge://twin/node/x")
        assert isinstance(parsed, ParsedResourceUri)


# ---------------------------------------------------------------------------
# Schemas — round-trip serialisation
# ---------------------------------------------------------------------------


class TestResourceSchemas:
    def test_manifest_minimal(self) -> None:
        m = ResourceManifest(
            uri_template="metaforge://twin/node/{node_id}",
            name="Twin node",
            description="Read a node",
            adapter_id="twin",
        )
        assert m.mime_type == "application/json"

    def test_content_text(self) -> None:
        c = ResourceContent(uri="metaforge://twin/node/abc", text="hello")
        assert c.text == "hello"
        assert c.blob_base64 is None

    def test_list_and_read_results(self) -> None:
        manifest = ResourceManifest(
            uri_template="metaforge://twin/node/{id}",
            name="n",
            description="d",
            adapter_id="twin",
        )
        ResourceListResult(resources=[manifest])
        ResourceReadResult(contents=[ResourceContent(uri="metaforge://twin/node/abc", text="x")])


# ---------------------------------------------------------------------------
# Server — register + dispatch
# ---------------------------------------------------------------------------


class _SampleServer(McpToolServer):
    """Bare-bones server that registers one resource with a closure matcher."""

    def __init__(self) -> None:
        super().__init__(adapter_id="sample", version="0.1.0")
        manifest = ResourceManifestEntry(
            uri_template="metaforge://sample/echo/{key}",
            name="Sample echo",
            description="Returns a static document keyed by URI suffix.",
            mime_type="application/json",
            adapter_id="sample",
        )
        self.register_resource(
            manifest=manifest,
            reader=self._read,
            matcher=lambda uri: uri.startswith("metaforge://sample/echo/"),
        )

    async def _read(self, uri: str) -> list[dict[str, Any]]:
        key = uri.rsplit("/", 1)[-1]
        return [
            {
                "uri": uri,
                "mime_type": "application/json",
                "text": json.dumps({"key": key, "ok": True}),
            }
        ]


def _request(method: str, params: dict[str, Any]) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": "1", "method": method, "params": params})


class TestRegistration:
    def test_register_resource_records_template(self) -> None:
        srv = _SampleServer()
        assert srv.resource_uri_templates == ["metaforge://sample/echo/{key}"]

    def test_duplicate_template_rejected(self) -> None:
        srv = _SampleServer()
        manifest = ResourceManifestEntry(
            uri_template="metaforge://sample/echo/{key}",
            name="dup",
            description="dup",
            adapter_id="sample",
        )
        with pytest.raises(ValueError, match="already registered"):
            srv.register_resource(
                manifest=manifest,
                reader=lambda uri: [],  # type: ignore[arg-type, return-value]
                matcher=lambda uri: True,
            )


class TestResourcesList:
    async def test_returns_registered_manifests(self) -> None:
        srv = _SampleServer()
        raw = await srv.handle_request(_request("resources/list", {}))
        body = json.loads(raw)
        result = body["result"]
        assert "resources" in result
        assert len(result["resources"]) == 1
        m = result["resources"][0]
        assert m["adapter_id"] == "sample"
        assert m["uri_template"] == "metaforge://sample/echo/{key}"

    async def test_adapter_filter(self) -> None:
        srv = _SampleServer()
        # Filter that doesn't match the registration's adapter_id => empty.
        raw = await srv.handle_request(_request("resources/list", {"adapter_id": "other"}))
        body = json.loads(raw)
        assert body["result"]["resources"] == []


class TestResourcesRead:
    async def test_dispatches_to_matching_reader(self) -> None:
        srv = _SampleServer()
        raw = await srv.handle_request(
            _request("resources/read", {"uri": "metaforge://sample/echo/hello"})
        )
        body = json.loads(raw)
        contents = body["result"]["contents"]
        assert len(contents) == 1
        assert contents[0]["uri"] == "metaforge://sample/echo/hello"
        payload = json.loads(contents[0]["text"])
        assert payload == {"key": "hello", "ok": True}

    async def test_unknown_uri_returns_resource_not_found(self) -> None:
        srv = _SampleServer()
        raw = await srv.handle_request(_request("resources/read", {"uri": "metaforge://other/x"}))
        body = json.loads(raw)
        assert "error" in body
        assert body["error"]["code"] == -32004  # RESOURCE_NOT_FOUND
        assert body["error"]["data"]["uri"] == "metaforge://other/x"

    async def test_missing_uri_returns_read_error(self) -> None:
        srv = _SampleServer()
        raw = await srv.handle_request(_request("resources/read", {}))
        body = json.loads(raw)
        assert "error" in body
        assert body["error"]["code"] == -32005  # RESOURCE_READ_ERROR

    async def test_reader_exception_wrapped_as_read_error(self) -> None:
        srv = _SampleServer()

        async def boom(uri: str) -> list[dict[str, Any]]:
            raise RuntimeError("backend down")

        manifest = ResourceManifestEntry(
            uri_template="metaforge://sample/boom/{x}",
            name="boom",
            description="raises",
            adapter_id="sample",
        )
        srv.register_resource(
            manifest=manifest,
            reader=boom,
            matcher=lambda uri: uri.startswith("metaforge://sample/boom/"),
        )

        raw = await srv.handle_request(
            _request("resources/read", {"uri": "metaforge://sample/boom/x"})
        )
        body = json.loads(raw)
        assert "error" in body
        assert body["error"]["code"] == -32005
        assert "backend down" in body["error"]["data"]["details"]


class TestUnknownMethodStillWorks:
    async def test_tool_list_still_dispatches(self) -> None:
        srv = _SampleServer()
        raw = await srv.handle_request(_request("tool/list", {}))
        body = json.loads(raw)
        # No tools registered, but the call should succeed.
        assert body["result"]["tools"] == []
