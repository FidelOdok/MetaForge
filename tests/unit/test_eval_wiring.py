"""Eval harness wiring guard (MET-10).

The eval flywheel wires each scenario's ``rubric`` list through three places in
``evals/run_scenarios.py`` (the dispatch, the per-rubric module, and the print
loop) plus the scenario JSON. That is easy to drift: add a rubric to a scenario
but forget the dispatch and it is *silently skipped* — the run still "passes"
while a whole dimension goes unscored. These tests fail fast on that class of
bug, in plain CI (no gateway needed).
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import pytest

_EVALS = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS))

_SCENARIOS = sorted(glob.glob(str(_EVALS / "scenarios" / "*.json")))
_RUN_SRC = (_EVALS / "run_scenarios.py").read_text(encoding="utf-8")

_REQUIRED_KEYS = {"id", "flow", "goal"}


def _rubrics(scenario: dict) -> list[str]:
    r = scenario.get("rubric")
    return [r] if isinstance(r, str) else list(r or [])


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_scenarios_exist() -> None:
    assert _SCENARIOS, "no scenario fixtures found under evals/scenarios/"


@pytest.mark.parametrize("path", _SCENARIOS, ids=[Path(p).stem for p in _SCENARIOS])
def test_scenario_has_required_fields(path: str) -> None:
    d = _load(path)
    missing = _REQUIRED_KEYS - d.keys()
    assert not missing, f"{Path(path).name}: missing required keys {missing}"
    assert d["id"] == Path(path).stem, f"{path}: id must match filename"
    assert isinstance(d.get("expected_deliverables", {}), dict)


@pytest.mark.parametrize("path", _SCENARIOS, ids=[Path(p).stem for p in _SCENARIOS])
def test_scenario_rubrics_resolve_to_modules(path: str) -> None:
    import importlib

    for name in _rubrics(_load(path)):
        mod = importlib.import_module(f"{name}_rubric")
        assert hasattr(mod, f"score_{name}"), f"{name}_rubric.py has no score_{name}()"
        assert hasattr(mod, f"evaluate_{name}"), f"{name}_rubric.py has no evaluate_{name}()"


# The tuple of rubric names the runner iterates when printing scores.
_PRINT_TUPLE = _RUN_SRC.split("for name in (", 1)[-1].split("):", 1)[0]


@pytest.mark.parametrize("path", _SCENARIOS, ids=[Path(p).stem for p in _SCENARIOS])
def test_scenario_rubrics_are_dispatched_and_printed(path: str) -> None:
    """Every rubric a scenario asks for must be run *and* reported by the runner."""
    for name in _rubrics(_load(path)):
        assert f'== "{name}"' in _RUN_SRC, (
            f"run_scenarios.py has no dispatch branch for rubric '{name}' "
            f"(scenario {Path(path).name} would silently skip it)"
        )
        assert f'"{name}"' in _PRINT_TUPLE, (
            f"rubric '{name}' is dispatched but missing from the runner's print loop"
        )
