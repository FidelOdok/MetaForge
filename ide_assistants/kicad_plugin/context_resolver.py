"""Resolve the currently selected PCB component to a ChatScope.

When a footprint or component is selected in the KiCad PCB editor, this
module maps it to a ``bom-entry`` scope whose ``entity_id`` is the
component reference designator (e.g. ``U1``, ``R3``).  When nothing is
selected the fallback is a ``project`` scope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .types import ChatScope

if TYPE_CHECKING:
    pass  # pcbnew types are only used for static analysis hints


# ---------------------------------------------------------------------------
# Core resolution logic (pure function — testable without pcbnew)
# ---------------------------------------------------------------------------


def resolve_scope_from_references(references: list[str]) -> ChatScope:
    """Map a list of selected component references to a ChatScope.

    Parameters
    ----------
    references:
        Reference designators of the selected footprints (e.g. ``["U1"]``).
        May be empty if nothing is selected.

    Returns
    -------
    ChatScope
        A ``bom-entry`` scope when references are provided, otherwise
        ``project``.
    """
    if not references:
        return ChatScope(kind="project", label="Project")

    if len(references) == 1:
        ref = references[0]
        return ChatScope(
            kind="bom-entry",
            entity_id=ref,
            label=f"Component: {ref}",
        )

    # Multiple components selected.
    joined = ", ".join(references[:5])
    suffix = f" (+{len(references) - 5} more)" if len(references) > 5 else ""
    return ChatScope(
        kind="bom-entry",
        entity_id=references[0],
        label=f"Components: {joined}{suffix}",
    )


# ---------------------------------------------------------------------------
# KiCad integration (requires pcbnew at runtime)
# ---------------------------------------------------------------------------


def get_selected_references() -> list[str]:
    """Return reference designators for all currently selected footprints.

    Requires ``pcbnew`` to be available (i.e. running inside KiCad).
    Returns an empty list when pcbnew is not importable or nothing is
    selected.
    """
    try:
        import pcbnew  # type: ignore[import-untyped]
    except ImportError:
        return []

    board = pcbnew.GetBoard()
    if board is None:
        return []

    refs: list[str] = []
    for footprint in board.GetFootprints():
        if footprint.IsSelected():
            refs.append(footprint.GetReference())

    return sorted(refs)


def resolve_current_scope() -> ChatScope:
    """Resolve the scope from the currently selected PCB footprints.

    Convenience function combining ``get_selected_references`` with
    ``resolve_scope_from_references``.
    """
    return resolve_scope_from_references(get_selected_references())
