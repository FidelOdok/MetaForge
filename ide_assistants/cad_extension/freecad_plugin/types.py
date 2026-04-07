"""Shared type definitions for the FreeCAD plugin."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChatScope:
    kind: str  # "cad-body" | "cad-assembly" | "cad-document"
    entity_id: str  # FreeCAD object Name or doc path
    label: str  # Human-readable label
    document_path: str  # Absolute path to .FCStd file


@dataclass
class PatchOp:
    op: str  # "set_property" | "add_feature" | "remove_feature" | "recompute"
    object_name: str  # FreeCAD object Name
    property: str | None = None
    value: object | None = None
