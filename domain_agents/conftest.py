"""Shared fixtures for the skill tests beside each handler (FORGE-446)."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def material_library() -> Any:
    """The test material library (see tests/support/material_library.py)."""
    from tests.support import material_library as lib
    from tool_registry.material_context import using
    from twin_core.library.material_resolver import install_material_source

    install_material_source(lib.source)
    with using(lib.figures()):
        yield
    install_material_source(None)
