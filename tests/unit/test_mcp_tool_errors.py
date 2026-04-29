"""Unit tests for ``mcp_core.errors`` (MET-385)."""

from __future__ import annotations

import json

import pytest

from mcp_core.errors import (
    RETRYABLE_CODES,
    ErrorCode,
    McpToolError,
    make_tool_error,
)


class TestErrorCode:
    def test_all_codes_are_strings(self) -> None:
        for code in ErrorCode:
            assert isinstance(code.value, str)
            # Wire format is the value; never break that.
            assert code.value == code

    def test_exactly_ten_codes(self) -> None:
        """The contract pins ten codes — adding more is a breaking
        change every harness has to absorb. Force a deliberate update.
        """
        assert len(list(ErrorCode)) == 10

    def test_codes_distinct(self) -> None:
        values = [code.value for code in ErrorCode]
        assert len(values) == len(set(values)), values

    @pytest.mark.parametrize(
        "code",
        [
            ErrorCode.BACKEND_UNAVAILABLE,
            ErrorCode.TIMEOUT,
            ErrorCode.RATE_LIMITED,
        ],
    )
    def test_transient_codes_are_retryable(self, code: ErrorCode) -> None:
        assert code in RETRYABLE_CODES

    @pytest.mark.parametrize(
        "code",
        [
            ErrorCode.INVALID_INPUT,
            ErrorCode.NOT_FOUND,
            ErrorCode.CONFLICT,
            ErrorCode.CONSTRAINT_VIOLATION,
            ErrorCode.AUTH_REQUIRED,
            ErrorCode.PERMISSION_DENIED,
            ErrorCode.INTERNAL,
        ],
    )
    def test_user_or_terminal_codes_not_retryable(self, code: ErrorCode) -> None:
        assert code not in RETRYABLE_CODES


class TestMcpToolError:
    def test_basic_construction(self) -> None:
        err = McpToolError(
            code=ErrorCode.NOT_FOUND,
            message="entry-id not found",
            retryable=False,
        )
        assert err.code == ErrorCode.NOT_FOUND
        assert err.retryable is False
        assert err.details is None
        assert err.trace_id is None

    def test_envelope_is_frozen(self) -> None:
        err = McpToolError(code=ErrorCode.INVALID_INPUT, message="bad", retryable=False)
        with pytest.raises((TypeError, ValueError)):
            err.message = "tampered"  # type: ignore[misc]

    def test_serialises_for_json_rpc_data(self) -> None:
        err = McpToolError(
            code=ErrorCode.CONSTRAINT_VIOLATION,
            message="rule R-7 failed",
            details={"rule_id": "R-7", "node_id": "abc"},
            retryable=False,
        )
        # Round-trip through JSON to confirm the wire shape is stable.
        body = json.loads(err.model_dump_json())
        assert body == {
            "code": "constraint_violation",
            "message": "rule R-7 failed",
            "details": {"rule_id": "R-7", "node_id": "abc"},
            "retryable": False,
            "trace_id": None,
        }


class TestMakeToolError:
    @pytest.mark.parametrize("code", list(ErrorCode))
    def test_default_retryable_matches_classification(self, code: ErrorCode) -> None:
        err = make_tool_error(code, "msg")
        assert err.retryable == (code in RETRYABLE_CODES)

    def test_retryable_override(self) -> None:
        # Tool-specific override: a normally-retryable code may be flagged
        # non-retryable for one specific tool when retry would loop forever.
        err = make_tool_error(ErrorCode.TIMEOUT, "long solver", retryable=False)
        assert err.retryable is False

    def test_details_propagate(self) -> None:
        err = make_tool_error(
            ErrorCode.INVALID_INPUT,
            "missing required field",
            details={"field": "mesh_file", "schema_path": "/properties/mesh_file"},
        )
        assert err.details == {
            "field": "mesh_file",
            "schema_path": "/properties/mesh_file",
        }

    def test_trace_id_propagates(self) -> None:
        err = make_tool_error(
            ErrorCode.INTERNAL,
            "unexpected",
            trace_id="abc123",
        )
        assert err.trace_id == "abc123"


class TestRoundTripFromJsonRpcEnvelope:
    """The envelope rides as ``error.data`` inside a JSON-RPC error.
    Verify a tool can produce one and a harness can parse it back.
    """

    def test_round_trip(self) -> None:
        # Tool side — produce.
        err = make_tool_error(
            ErrorCode.BACKEND_UNAVAILABLE,
            "Postgres is down",
            details={"service": "postgres", "host": "metaforge-postgres-1"},
        )
        # Wrap into a JSON-RPC envelope.
        envelope = {
            "jsonrpc": "2.0",
            "id": "req-42",
            "error": {
                "code": -32001,  # JSON-RPC tool-execution code
                "message": "Tool execution failed",
                "data": err.model_dump(mode="json"),
            },
        }
        wire = json.dumps(envelope)

        # Harness side — parse + branch.
        parsed = json.loads(wire)
        tool_err = McpToolError.model_validate(parsed["error"]["data"])
        assert tool_err.code == ErrorCode.BACKEND_UNAVAILABLE
        assert tool_err.retryable is True  # inferred default
        assert tool_err.details == {
            "service": "postgres",
            "host": "metaforge-postgres-1",
        }
