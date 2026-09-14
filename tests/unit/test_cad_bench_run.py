"""Unit tests for evals/cad_bench/run_cad_bench.py's pure logic (MET-704)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[2] / "evals" / "cad_bench" / "run_cad_bench.py"
_spec = importlib.util.spec_from_file_location("run_cad_bench", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
run_cad_bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_cad_bench)

bridge_to_result = run_cad_bench.bridge_to_result


class TestBridgeToResult:
    def test_appends_result_for_the_last_assigned_variable(self):
        script = "import cadquery as cq\nr = cq.Workplane('XY').box(1, 1, 1)\n"
        out = bridge_to_result(script)
        assert out.strip().splitlines()[-1] == "result = r"

    def test_leaves_a_script_that_already_assigns_result_untouched(self):
        script = "import cadquery as cq\nresult = cq.Workplane('XY').box(1, 1, 1)\n"
        assert bridge_to_result(script) == script

    def test_uses_the_last_assignment_not_the_first(self):
        script = "a = 1\nb = 2\nr = a + b\n"
        out = bridge_to_result(script)
        assert out.strip().splitlines()[-1] == "result = r"

    def test_empty_or_assignment_free_script_is_returned_unchanged(self):
        script = "# just a comment\n"
        assert bridge_to_result(script) == script
