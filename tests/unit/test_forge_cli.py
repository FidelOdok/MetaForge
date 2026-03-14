"""Unit tests for the MetaForge Python CLI (MET-39).

Covers:
- ForgeClient methods (with mocked httpx responses)
- CLI argument parsing
- Output formatters (table, json, compact)
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from cli.forge_cli.client import ForgeClient
from cli.forge_cli.formatters import (
    format_compact,
    format_json,
    format_output,
    format_table,
)
from cli.forge_cli.main import _parse_params, build_parser

# ===================================================================
# Formatter tests
# ===================================================================


class TestFormatJson:
    def test_simple_dict(self) -> None:
        result = format_json({"key": "value"})
        parsed = json.loads(result)
        assert parsed == {"key": "value"}

    def test_list(self) -> None:
        result = format_json([1, 2, 3])
        parsed = json.loads(result)
        assert parsed == [1, 2, 3]

    def test_nested(self) -> None:
        data = {"a": {"b": [1, 2]}}
        result = format_json(data)
        parsed = json.loads(result)
        assert parsed["a"]["b"] == [1, 2]

    def test_indented(self) -> None:
        result = format_json({"key": "value"})
        assert "\n" in result  # indented output


class TestFormatTable:
    def test_empty_rows(self) -> None:
        assert format_table([]) == "(no results)"

    def test_single_row(self) -> None:
        rows = [{"id": "abc", "status": "ok"}]
        result = format_table(rows)
        assert "ID" in result
        assert "STATUS" in result
        assert "abc" in result
        assert "ok" in result

    def test_multiple_rows(self) -> None:
        rows = [
            {"id": "1", "name": "alpha"},
            {"id": "2", "name": "beta"},
        ]
        result = format_table(rows)
        lines = result.strip().split("\n")
        assert len(lines) == 4  # header + separator + 2 rows

    def test_custom_columns(self) -> None:
        rows = [{"a": 1, "b": 2, "c": 3}]
        result = format_table(rows, columns=["c", "a"])
        header = result.split("\n")[0]
        # C should come before A
        assert header.index("C") < header.index("A")

    def test_alignment(self) -> None:
        rows = [
            {"id": "short", "name": "x"},
            {"id": "much-longer-id", "name": "y"},
        ]
        result = format_table(rows)
        lines = result.strip().split("\n")
        # All non-empty lines should have consistent structure
        assert len(lines) == 4


class TestFormatCompact:
    def test_empty_rows(self) -> None:
        assert format_compact([]) == "(no results)"

    def test_single_row(self) -> None:
        rows = [{"id": "abc", "status": "ok"}]
        result = format_compact(rows)
        assert "abc:" in result
        assert "status=ok" in result

    def test_custom_key_field(self) -> None:
        rows = [{"name": "test", "value": 42}]
        result = format_compact(rows, key_field="name")
        assert "test:" in result

    def test_multiple_rows(self) -> None:
        rows = [
            {"id": "1", "a": "x"},
            {"id": "2", "a": "y"},
        ]
        result = format_compact(rows)
        lines = result.strip().split("\n")
        assert len(lines) == 2


class TestFormatOutput:
    def test_json_mode(self) -> None:
        result = format_output({"key": "val"}, fmt="json")
        assert json.loads(result) == {"key": "val"}

    def test_table_mode_list(self) -> None:
        data = [{"id": "1"}]
        result = format_output(data, fmt="table")
        assert "ID" in result

    def test_compact_mode_list(self) -> None:
        data = [{"id": "1", "status": "ok"}]
        result = format_output(data, fmt="compact")
        assert "1:" in result

    def test_json_mode_non_list(self) -> None:
        result = format_output({"x": 1}, fmt="compact")
        # Non-list falls back to json
        parsed = json.loads(result)
        assert parsed == {"x": 1}

    def test_table_mode_non_list(self) -> None:
        result = format_output({"x": 1}, fmt="table")
        # Non-list falls back to json
        parsed = json.loads(result)
        assert parsed == {"x": 1}


# ===================================================================
# Argument parser tests
# ===================================================================


class TestBuildParser:
    def test_run_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["run", "validate_stress", "--work_product", str(uuid4())])
        assert args.command == "run"
        assert args.skill_name == "validate_stress"

    def test_run_with_params(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "run",
                "validate_stress",
                "--work_product",
                str(uuid4()),
                "--params",
                '{"load": 500}',
            ]
        )
        assert args.params == '{"load": 500}'

    def test_run_with_session_id(self) -> None:
        parser = build_parser()
        sid = str(uuid4())
        args = parser.parse_args(
            [
                "run",
                "check_bom",
                "--work_product",
                str(uuid4()),
                "--session-id",
                sid,
            ]
        )
        assert args.session_id == sid

    def test_status_command(self) -> None:
        parser = build_parser()
        sid = str(uuid4())
        args = parser.parse_args(["status", sid])
        assert args.command == "status"
        assert args.session_id == sid

    def test_twin_query_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["twin", "query", "node-123"])
        assert args.command == "twin"
        assert args.twin_command == "query"
        assert args.node_id == "node-123"

    def test_twin_list_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "twin",
                "list",
                "--domain",
                "mechanical",
                "--type",
                "cad_model",
            ]
        )
        assert args.command == "twin"
        assert args.twin_command == "list"
        assert args.domain == "mechanical"
        assert args.work_product_type == "cad_model"

    def test_proposals_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["proposals"])
        assert args.command == "proposals"

    def test_approve_command(self) -> None:
        parser = build_parser()
        cid = str(uuid4())
        args = parser.parse_args(["approve", cid, "--reason", "looks good"])
        assert args.command == "approve"
        assert args.change_id == cid
        assert args.reason == "looks good"
        assert args.reviewer == "cli-user"  # default

    def test_reject_command(self) -> None:
        parser = build_parser()
        cid = str(uuid4())
        args = parser.parse_args(
            [
                "reject",
                cid,
                "--reason",
                "needs work",
                "--reviewer",
                "bob",
            ]
        )
        assert args.command == "reject"
        assert args.reason == "needs work"
        assert args.reviewer == "bob"

    def test_format_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--format", "json", "proposals"])
        assert args.output_format == "json"

    def test_gateway_url_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--gateway-url", "http://gw:9000", "proposals"])
        assert args.gateway_url == "http://gw:9000"

    def test_no_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args([])
        assert args.command is None


class TestParseParams:
    def test_valid_json(self) -> None:
        result = _parse_params('{"load": 500}')
        assert result == {"load": 500}

    def test_empty_object(self) -> None:
        result = _parse_params("{}")
        assert result == {}

    def test_invalid_json(self) -> None:
        with pytest.raises(SystemExit):
            _parse_params("not json")

    def test_non_object_json(self) -> None:
        with pytest.raises(SystemExit):
            _parse_params("[1, 2, 3]")


# ===================================================================
# ForgeClient tests (mocked httpx)
# ===================================================================


def _mock_response(data: dict[str, Any], status_code: int = 200) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status.return_value = None
    return resp


class TestForgeClientDefaults:
    def test_default_base_url(self) -> None:
        client = ForgeClient()
        assert client.base_url == "http://localhost:8000"

    def test_custom_base_url(self) -> None:
        client = ForgeClient(base_url="http://custom:9000")
        assert client.base_url == "http://custom:9000"

    def test_env_var_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("METAFORGE_GATEWAY_URL", "http://env:8080")
        client = ForgeClient()
        assert client.base_url == "http://env:8080"

    def test_explicit_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("METAFORGE_GATEWAY_URL", "http://env:8080")
        client = ForgeClient(base_url="http://explicit:9999")
        assert client.base_url == "http://explicit:9999"


class TestForgeClientRunSkill:
    @patch("cli.forge_cli.client.httpx.Client")
    def test_run_skill(self, mock_client_cls: MagicMock) -> None:
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_ctx.post.return_value = _mock_response({"status": "accepted"})
        mock_client_cls.return_value = mock_ctx

        fc = ForgeClient()
        result = fc.run_skill("validate_stress", str(uuid4()), {"load": 500})
        assert result["status"] == "accepted"
        mock_ctx.post.assert_called_once()

    @patch("cli.forge_cli.client.httpx.Client")
    def test_run_skill_with_session(self, mock_client_cls: MagicMock) -> None:
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_ctx.post.return_value = _mock_response({"status": "accepted"})
        mock_client_cls.return_value = mock_ctx

        fc = ForgeClient()
        sid = str(uuid4())
        fc.run_skill("check_bom", str(uuid4()), session_id=sid)
        call_args = mock_ctx.post.call_args
        payload = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1]
        assert payload["session_id"] == sid


class TestForgeClientProposals:
    @patch("cli.forge_cli.client.httpx.Client")
    def test_list_proposals(self, mock_client_cls: MagicMock) -> None:
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_ctx.get.return_value = _mock_response({"proposals": [], "total": 0})
        mock_client_cls.return_value = mock_ctx

        fc = ForgeClient()
        result = fc.list_proposals()
        assert result["total"] == 0

    @patch("cli.forge_cli.client.httpx.Client")
    def test_approve_proposal(self, mock_client_cls: MagicMock) -> None:
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_ctx.post.return_value = _mock_response({"status": "approved"})
        mock_client_cls.return_value = mock_ctx

        fc = ForgeClient()
        cid = str(uuid4())
        result = fc.approve_proposal(cid, "looks good")
        assert result["status"] == "approved"

    @patch("cli.forge_cli.client.httpx.Client")
    def test_reject_proposal(self, mock_client_cls: MagicMock) -> None:
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_ctx.post.return_value = _mock_response({"status": "rejected"})
        mock_client_cls.return_value = mock_ctx

        fc = ForgeClient()
        cid = str(uuid4())
        result = fc.reject_proposal(cid, "needs work", reviewer="bob")
        assert result["status"] == "rejected"


class TestForgeClientTwin:
    @patch("cli.forge_cli.client.httpx.Client")
    def test_twin_query(self, mock_client_cls: MagicMock) -> None:
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_ctx.get.return_value = _mock_response({"id": "node-1", "type": "cad_model"})
        mock_client_cls.return_value = mock_ctx

        fc = ForgeClient()
        result = fc.twin_query("node-1")
        assert result["id"] == "node-1"

    @patch("cli.forge_cli.client.httpx.Client")
    def test_twin_list(self, mock_client_cls: MagicMock) -> None:
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_ctx.get.return_value = _mock_response({"nodes": [], "total": 0})
        mock_client_cls.return_value = mock_ctx

        fc = ForgeClient()
        result = fc.twin_list(domain="mechanical")
        assert result["total"] == 0

    @patch("cli.forge_cli.client.httpx.Client")
    def test_twin_list_with_type(self, mock_client_cls: MagicMock) -> None:
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_ctx.get.return_value = _mock_response({"nodes": [], "total": 0})
        mock_client_cls.return_value = mock_ctx

        fc = ForgeClient()
        fc.twin_list(domain="electronics", work_product_type="schematic")
        call_args = mock_ctx.get.call_args
        params = call_args[1].get("params", {})
        assert params.get("domain") == "electronics"
        assert params.get("type") == "schematic"
