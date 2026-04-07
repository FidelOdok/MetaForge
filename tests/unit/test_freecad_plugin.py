"""Unit tests for the FreeCAD plugin — no FreeCAD dependency required."""

from ide_assistants.cad_extension.freecad_plugin.context_resolver import (
    get_document_path,
    get_selected_names,
    resolve_scope_from_selection,
)
from ide_assistants.cad_extension.freecad_plugin.types import ChatScope


def test_empty_selection_returns_document_scope() -> None:
    scope = resolve_scope_from_selection([], "/path/to/model.FCStd", "MyModel")
    assert scope.kind == "cad-document"
    assert scope.document_path == "/path/to/model.FCStd"


def test_single_selection_returns_body_scope() -> None:
    scope = resolve_scope_from_selection(["Body"], "/path/to/model.FCStd", "MyModel")
    assert scope.kind == "cad-body"
    assert scope.entity_id == "Body"
    assert scope.label == "Body"


def test_multi_selection_returns_assembly_scope() -> None:
    scope = resolve_scope_from_selection(["Body", "Pad"], "/path/to/model.FCStd", "MyModel")
    assert scope.kind == "cad-assembly"
    assert "2" in scope.label


def test_document_path_preserved() -> None:
    scope = resolve_scope_from_selection(["Body"], "/workspace/drone.FCStd", "Drone")
    assert scope.document_path == "/workspace/drone.FCStd"


def test_get_selected_names_returns_empty_without_freecad() -> None:
    # FreeCAD not installed in test env — should return [] gracefully
    result = get_selected_names()
    assert result == []


def test_get_document_path_returns_empty_without_freecad() -> None:
    result = get_document_path()
    assert result == ""


def test_chat_scope_dataclass_fields() -> None:
    scope = ChatScope(
        kind="cad-body",
        entity_id="Body001",
        label="Body001",
        document_path="/tmp/test.FCStd",
    )
    assert scope.kind == "cad-body"
    assert scope.entity_id == "Body001"
    assert scope.label == "Body001"
    assert scope.document_path == "/tmp/test.FCStd"


def test_assembly_entity_id_is_comma_joined() -> None:
    scope = resolve_scope_from_selection(
        ["Body", "Pad", "Pocket"],
        "/path/model.FCStd",
        "Model",
    )
    assert scope.entity_id == "Body,Pad,Pocket"


def test_document_scope_entity_id_is_path() -> None:
    scope = resolve_scope_from_selection([], "/path/model.FCStd", "Model")
    assert scope.entity_id == "/path/model.FCStd"
