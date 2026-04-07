"""Resolves the active FreeCAD selection to a ChatScope."""

# mypy: warn-unused-ignores = False
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from ide_assistants.cad_extension.freecad_plugin.types import ChatScope


def resolve_scope_from_selection(
    selected_names: list[str],
    document_path: str,
    document_name: str,
) -> ChatScope:
    """Pure function: map selected object names to a ChatScope.

    Args:
        selected_names: list of FreeCAD object Names currently selected
        document_path: absolute path to the .FCStd file
        document_name: FreeCAD document Label

    Returns a ChatScope for the selection.
    """
    if not selected_names:
        return ChatScope(
            kind="cad-document",
            entity_id=document_path,
            label=document_name,
            document_path=document_path,
        )
    if len(selected_names) == 1:
        return ChatScope(
            kind="cad-body",
            entity_id=selected_names[0],
            label=selected_names[0],
            document_path=document_path,
        )
    return ChatScope(
        kind="cad-assembly",
        entity_id=",".join(selected_names),
        label=f"{len(selected_names)} objects",
        document_path=document_path,
    )


def get_selected_names() -> list[str]:
    """Get currently selected object Names from FreeCAD. Returns [] if FreeCAD unavailable."""
    try:
        import FreeCADGui  # type: ignore[import-not-found]

        sel = FreeCADGui.Selection.getSelection()
        return [obj.Name for obj in sel]
    except Exception:
        return []


def get_document_path() -> str:
    """Get the path of the active document. Returns '' if none open."""
    try:
        import FreeCAD  # type: ignore[import-not-found]

        doc = FreeCAD.ActiveDocument
        return doc.FileName if doc else ""
    except Exception:
        return ""


def get_document_name() -> str:
    """Get the label of the active document."""
    try:
        import FreeCAD  # type: ignore[import-not-found]

        doc = FreeCAD.ActiveDocument
        return doc.Label if doc else "Untitled"
    except Exception:
        return "Untitled"


def resolve_current_scope() -> ChatScope:
    """Resolve the current FreeCAD selection to a ChatScope."""
    names = get_selected_names()
    path = get_document_path()
    label = get_document_name()
    return resolve_scope_from_selection(names, path, label)
