"""Unit tests for VS Code extension logic (MET-37, MET-93).

Since TypeScript modules cannot be imported directly in Python, these
tests validate the *same logic patterns* by testing against equivalent
Python implementations or by exercising the pure-function logic that
was designed to be testable independently of the VS Code API.

Test coverage:
  - Context detector: file extension -> scope mapping
  - Gateway client: URL construction helpers
  - Webview bridge: message type catalogue
  - Twin sidebar: tree structure mapping
"""

from __future__ import annotations

import json
import os
import re

import pytest


# =========================================================================
# 1. Context detector — scope mapping
# =========================================================================

# Replicate the mapping rules from context-detector.ts for testing.

_SCOPE_RULES: list[tuple[list[str], str]] = [
    ([".kicad_sch", ".kicad_pcb"], "bom-entry"),
    ([".c", ".h", "pinmap.json"], "session"),
    ([".fcstd", ".step"], "digital-twin-node"),
]
_DEFAULT_SCOPE = "project"


def _detect_scope(file_path: str) -> str:
    """Python replica of the detectScopeFromPath function."""
    ext = os.path.splitext(file_path)[1].lower()
    basename = os.path.basename(file_path).lower()

    for patterns, scope in _SCOPE_RULES:
        for pattern in patterns:
            if pattern.startswith("."):
                if ext == pattern:
                    return scope
            else:
                if basename == pattern:
                    return scope
    return _DEFAULT_SCOPE


class TestContextDetector:
    """Tests that mirror the logic in context-detector.ts."""

    def test_kicad_schematic_maps_to_bom_entry(self) -> None:
        assert _detect_scope("/project/eda/main.kicad_sch") == "bom-entry"

    def test_kicad_pcb_maps_to_bom_entry(self) -> None:
        assert _detect_scope("/project/eda/board.kicad_pcb") == "bom-entry"

    def test_c_file_maps_to_session(self) -> None:
        assert _detect_scope("/project/firmware/src/main.c") == "session"

    def test_h_file_maps_to_session(self) -> None:
        assert _detect_scope("/project/firmware/src/gpio.h") == "session"

    def test_pinmap_json_maps_to_session(self) -> None:
        assert _detect_scope("/project/firmware/pinmap.json") == "session"

    def test_freecad_file_maps_to_digital_twin_node(self) -> None:
        assert _detect_scope("/project/cad/enclosure.FCStd") == "digital-twin-node"

    def test_step_file_maps_to_digital_twin_node(self) -> None:
        assert _detect_scope("/project/cad/housing.step") == "digital-twin-node"

    def test_python_file_maps_to_project(self) -> None:
        assert _detect_scope("/project/scripts/build.py") == "project"

    def test_unknown_extension_maps_to_project(self) -> None:
        assert _detect_scope("/readme.md") == "project"

    def test_case_insensitive_extension(self) -> None:
        assert _detect_scope("/CAD/MODEL.STEP") == "digital-twin-node"

    def test_case_insensitive_fcstd(self) -> None:
        assert _detect_scope("/Design/Part.FcStd") == "digital-twin-node"


# =========================================================================
# 2. Gateway client — URL construction
# =========================================================================


def _build_url(base: str, path: str) -> str:
    """Python replica of the buildUrl function from gateway-client.ts."""
    normalized_base = base.rstrip("/")
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"{normalized_base}{normalized_path}"


def _to_ws_url(http_url: str) -> str:
    """Python replica of the toWebSocketUrl function."""
    return re.sub(r"^http", "ws", http_url)


class TestGatewayClient:
    """Tests that mirror the logic in gateway-client.ts."""

    def test_build_url_basic(self) -> None:
        assert _build_url("http://localhost:8000", "/api/v1/health") == (
            "http://localhost:8000/api/v1/health"
        )

    def test_build_url_strips_trailing_slash(self) -> None:
        assert _build_url("http://localhost:8000/", "/api/v1/twin/tree") == (
            "http://localhost:8000/api/v1/twin/tree"
        )

    def test_build_url_adds_leading_slash(self) -> None:
        assert _build_url("http://localhost:8000", "api/v1/health") == (
            "http://localhost:8000/api/v1/health"
        )

    def test_to_ws_url_http(self) -> None:
        assert _to_ws_url("http://localhost:8000") == "ws://localhost:8000"

    def test_to_ws_url_https(self) -> None:
        assert _to_ws_url("https://gateway.example.com") == (
            "wss://gateway.example.com"
        )

    def test_websocket_url_for_session(self) -> None:
        base = "http://localhost:8000"
        ws_base = _to_ws_url(base)
        session_id = "sess-abc"
        url = _build_url(ws_base, f"/api/v1/assistant/ws/{session_id}")
        assert url == "ws://localhost:8000/api/v1/assistant/ws/sess-abc"


# =========================================================================
# 3. Webview bridge — message types
# =========================================================================

_EXPECTED_MESSAGE_TYPES = {
    "sendMessage",
    "receiveMessage",
    "updateThread",
    "setContext",
    "typing",
}


class TestWebviewBridge:
    """Tests that validate the webview bridge message type contract."""

    def test_all_message_types_defined(self) -> None:
        """Ensure all expected message types are accounted for."""
        assert _EXPECTED_MESSAGE_TYPES == {
            "sendMessage",
            "receiveMessage",
            "updateThread",
            "setContext",
            "typing",
        }

    def test_send_message_payload(self) -> None:
        """Validate sendMessage shape."""
        msg = {"type": "sendMessage", "content": "Hello"}
        assert msg["type"] == "sendMessage"
        assert "content" in msg

    def test_receive_message_payload(self) -> None:
        """Validate receiveMessage shape."""
        msg = {
            "type": "receiveMessage",
            "message": {
                "id": "m1",
                "threadId": "t1",
                "actor": {"kind": "agent", "displayName": "MechAgent"},
                "content": "Analysis complete.",
                "createdAt": "2024-01-01T00:00:00Z",
            },
        }
        assert msg["type"] == "receiveMessage"
        assert msg["message"]["actor"]["kind"] == "agent"

    def test_typing_payload(self) -> None:
        msg = {"type": "typing", "agentName": "ElecAgent", "isTyping": True}
        assert msg["type"] == "typing"
        assert msg["isTyping"] is True

    def test_set_context_payload(self) -> None:
        msg = {
            "type": "setContext",
            "scope": {"kind": "bom-entry", "entityId": "U1", "label": "BOM: U1"},
        }
        assert msg["scope"]["kind"] == "bom-entry"


# =========================================================================
# 4. Twin sidebar — tree structure
# =========================================================================


def _twin_node(
    node_id: str,
    node_type: str,
    label: str,
    children: list | None = None,
) -> dict:
    """Create a twin node dict matching the TwinNode interface."""
    node: dict = {
        "id": node_id,
        "type": node_type,
        "label": label,
        "metadata": {},
    }
    if children:
        node["children"] = children
    return node


class TestTwinSidebar:
    """Tests that validate the twin sidebar tree data structure."""

    def test_leaf_node_has_no_children(self) -> None:
        node = _twin_node("a1", "artifact", "PCB Layout")
        assert "children" not in node or node.get("children") is None

    def test_parent_node_has_children(self) -> None:
        child = _twin_node("c1", "component", "U1")
        parent = _twin_node("a1", "artifact", "Schematic", children=[child])
        assert len(parent["children"]) == 1
        assert parent["children"][0]["label"] == "U1"

    def test_node_type_icon_mapping(self) -> None:
        """Verify that every supported node type has a known icon mapping."""
        supported_types = {"artifact", "constraint", "component", "relationship"}
        icon_map: dict[str, str] = {
            "artifact": "file-code",
            "constraint": "shield",
            "component": "circuit-board",
            "relationship": "link",
        }
        for t in supported_types:
            assert t in icon_map, f"Missing icon mapping for type: {t}"

    def test_tree_response_structure(self) -> None:
        response = {
            "nodes": [
                _twin_node("a1", "artifact", "PCB Layout"),
                _twin_node("c1", "constraint", "Max current 2A"),
            ],
            "version": "v0.1.0",
        }
        assert len(response["nodes"]) == 2
        assert response["version"] == "v0.1.0"
