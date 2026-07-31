"""Chat-eval harness wiring guard (MET-570).

Sibling of ``test_eval_wiring.py`` for the multi-turn chat suite: every rubric
a chat scenario declares must resolve to a module, be dispatched by
``run_chat_scenarios.py``, and appear in its print loop — otherwise a whole
scoring dimension silently disappears. Additionally the scenario JSONs are
linted: check ids unique and referenced by ``expected_today``, check types
within the supported set, repeat blocks well-formed. Plain CI, no gateway.
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import pytest

_EVALS = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(_EVALS))

from chat_rubric_common import SUPPORTED_CHECK_TYPES  # noqa: E402

_SCENARIOS = sorted(glob.glob(str(_EVALS / "chat_scenarios" / "*.json")))
_RUN_SRC = (_EVALS / "run_chat_scenarios.py").read_text(encoding="utf-8")

_REQUIRED_KEYS = {"id", "title", "scope", "rubric", "turns"}


def _rubrics(scenario: dict) -> list[str]:
    r = scenario.get("rubric")
    return [r] if isinstance(r, str) else list(r or [])


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _declared_checks(scenario: dict) -> list[dict]:
    return [c for t in scenario["turns"] for c in t.get("checks") or []]


def _structural_ids(scenario: dict) -> set[str]:
    import importlib

    ids: set[str] = set()
    for name in _rubrics(scenario):
        mod = importlib.import_module(f"{name}_rubric")
        ids.update(getattr(mod, "CHECKS", ()))
    return ids


def test_scenarios_exist() -> None:
    assert _SCENARIOS, "no scenario fixtures found under evals/chat_scenarios/"


@pytest.mark.parametrize("path", _SCENARIOS, ids=[Path(p).stem for p in _SCENARIOS])
def test_scenario_has_required_fields(path: str) -> None:
    d = _load(path)
    missing = _REQUIRED_KEYS - d.keys()
    assert not missing, f"{Path(path).name}: missing required keys {missing}"
    assert d["id"] == Path(path).stem, f"{path}: id must match filename"
    assert d["scope"] in {"assistant", "project"}, f"{path}: unknown scope {d['scope']!r}"
    assert isinstance(d["turns"], list) and d["turns"], f"{path}: turns must be non-empty"


@pytest.mark.parametrize("path", _SCENARIOS, ids=[Path(p).stem for p in _SCENARIOS])
def test_scenario_rubrics_resolve_to_modules(path: str) -> None:
    import importlib

    for name in _rubrics(_load(path)):
        mod = importlib.import_module(f"{name}_rubric")
        assert hasattr(mod, f"score_{name}"), f"{name}_rubric.py has no score_{name}()"
        assert hasattr(mod, f"evaluate_{name}"), f"{name}_rubric.py has no evaluate_{name}()"
        assert getattr(mod, "CHECKS", ()), f"{name}_rubric.py must declare a CHECKS tuple"


# The tuple of rubric names the runner iterates when printing scores.
_PRINT_TUPLE = _RUN_SRC.split("for name in (", 1)[-1].split("):", 1)[0]


@pytest.mark.parametrize("path", _SCENARIOS, ids=[Path(p).stem for p in _SCENARIOS])
def test_scenario_rubrics_are_dispatched_and_printed(path: str) -> None:
    """Every rubric a scenario asks for must be run *and* reported by the runner."""
    for name in _rubrics(_load(path)):
        assert f'== "{name}"' in _RUN_SRC, (
            f"run_chat_scenarios.py has no dispatch branch for rubric '{name}' "
            f"(scenario {Path(path).name} would silently skip it)"
        )
        assert f'"{name}"' in _PRINT_TUPLE, (
            f"rubric '{name}' is dispatched but missing from the runner's print loop"
        )


@pytest.mark.parametrize("path", _SCENARIOS, ids=[Path(p).stem for p in _SCENARIOS])
def test_turns_and_checks_well_formed(path: str) -> None:
    d = _load(path)
    seen_ids: set[str] = set()
    for turn in d["turns"]:
        rep = turn.get("repeat")
        if rep is not None:
            assert set(rep) == {"count", "template"}, f"{path}: malformed repeat block {rep}"
            assert isinstance(rep["count"], int) and rep["count"] > 0
            assert isinstance(rep["template"], str) and rep["template"]
            assert "checks" not in turn, f"{path}: repeat blocks cannot carry checks"
            continue
        assert isinstance(turn.get("say"), str) and turn["say"], f"{path}: turn without say"
        for check in turn.get("checks") or []:
            cid = check.get("id")
            assert cid and cid not in seen_ids, f"{path}: duplicate/missing check id {cid!r}"
            seen_ids.add(cid)
            assert check.get("type") in SUPPORTED_CHECK_TYPES, (
                f"{path}: check {cid} has unsupported type {check.get('type')!r}"
            )


@pytest.mark.parametrize("path", _SCENARIOS, ids=[Path(p).stem for p in _SCENARIOS])
def test_expected_today_references_known_checks(path: str) -> None:
    """xfail predictions must point at a declared or structural check id."""
    d = _load(path)
    known = {str(c["id"]) for c in _declared_checks(d)} | _structural_ids(d)
    for cid, exp in (d.get("expected_today") or {}).items():
        assert cid in known, f"{path}: expected_today references unknown check '{cid}'"
        values = list(exp.values()) if isinstance(exp, dict) else [exp]
        assert all(v == "fail" for v in values), (
            f"{path}: expected_today['{cid}'] must be 'fail' (got {exp!r})"
        )
        if isinstance(exp, dict):
            variants = {v.get("name") for v in d.get("variants") or []}
            unknown = set(exp) - variants
            assert not unknown, f"{path}: expected_today['{cid}'] names unknown variants {unknown}"


@pytest.mark.parametrize("path", _SCENARIOS, ids=[Path(p).stem for p in _SCENARIOS])
def test_declared_ids_do_not_shadow_structural_checks(path: str) -> None:
    d = _load(path)
    declared = {str(c["id"]) for c in _declared_checks(d)}
    clash = declared & _structural_ids(d)
    assert not clash, f"{path}: declared check ids shadow rubric structural checks {clash}"


@pytest.mark.parametrize("path", _SCENARIOS, ids=[Path(p).stem for p in _SCENARIOS])
def test_variant_names_unique(path: str) -> None:
    d = _load(path)
    names = [v.get("name") for v in d.get("variants") or []]
    assert len(names) == len(set(names)), f"{path}: duplicate variant names {names}"
