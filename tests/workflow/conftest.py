"""Shared fixtures for the workflow suites (FORGE-562)."""

from __future__ import annotations

import pytest

from tests.workflow.scenarios import bracket


@pytest.fixture()
def accepted_bracket() -> bracket.Accepted:
    """A fresh accepted bracket workflow: the lifecycle suite's starting point."""
    return bracket.accept()
