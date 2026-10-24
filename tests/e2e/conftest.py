"""Shared fixtures for the e2e suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _kicad_design_paths_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    """These flows stub kicad-cli's execution, so their design files are notional.

    The KiCad adapter refuses a path that does not exist (FORGE-680); that
    refusal is tested in tests/unit/test_kicad_adapter.py with real files.
    """
    from tool_registry.tools.kicad.adapter import KicadServer

    monkeypatch.setattr(KicadServer, "_existing", lambda self, path, kind: path)
