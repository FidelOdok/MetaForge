"""The authored-form runtime: the sandbox and the shape builder (spec 23.6).

The runtime is JavaScript (``api_gateway/imagine/engine/authored.js``) because it
runs in the page. These tests run its real worker source under node, through
``imagine_authored_runtime_check.mjs``, and report each check on its own so a
failure names the property that broke.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
SCRIPT = HERE / "imagine_authored_runtime_check.mjs"

FORBIDDEN_GLOBALS = [
    "fetch",
    "XMLHttpRequest",
    "WebSocket",
    "EventSource",
    "importScripts",
    "indexedDB",
    "caches",
    "navigator",
    "postMessage",
    "eval",
    "setTimeout",
    "setInterval",
    "queueMicrotask",
    "BroadcastChannel",
    "SharedWorker",
    "Worker",
]

CHECKS = [
    "lamp_parts_draw_with_the_expected_bounding_boxes",
    "a_parameter_change_changes_the_geometry_with_no_model_call",
    "the_same_input_gives_the_same_output",
    *[f"sandbox_removes_{name}" for name in FORBIDDEN_GLOBALS],
    "sandbox_this_is_undefined_in_strict_code",
    "sandbox_parameters_are_frozen",
    "sandbox_builder_offers_only_the_documented_methods",
    "an_endless_loop_is_stopped_and_named",
    "a_busy_page_does_not_count_against_the_part",
    "output_over_the_triangle_budget_is_refused_naming_the_part",
    "a_throwing_part_is_reported_and_the_others_still_draw",
    "a_syntax_error_in_one_part_does_not_take_down_the_others",
    "code_that_closes_its_function_early_cannot_run_before_the_sandbox",
    "returning_something_that_is_not_a_list_of_shapes_is_an_error",
    "sanitize_refuses_bad_numbers_bad_indices_and_oversized_output",
    "box_has_twelve_triangles_and_cylinder_and_sphere_are_closed_shapes",
    "primitives_are_wound_outward_and_have_the_right_size",
    "rotation_is_in_degrees_about_x_then_y_then_z",
    "extrude_triangulates_a_concave_outline",
    "lathe_and_torus_and_tube_and_frustum_build",
    "a_builder_call_with_a_bad_argument_says_which_one",
    "describe_states_what_was_measured_and_what_was_not_assessed",
    "describe_says_a_form_was_not_or_only_partly_drawn_and_why",
    "summary_block_renders_the_description_into_the_page",
    "sliders_can_land_exactly_on_the_declared_values_and_on_whole_numbers",
    "quick_panel_uses_the_engines_own_markup_and_still_redraws",
    "mount_draws_publishes_a_summary_and_redraws_on_a_parameter_change",
]


@pytest.fixture(scope="module")
def results() -> dict[str, dict[str, object]]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; the authored-form runtime cannot be exercised")
    proc = subprocess.run(
        [node, str(SCRIPT)], capture_output=True, text=True, timeout=120, cwd=HERE.parent.parent
    )
    out: dict[str, dict[str, object]] = {}
    for line in proc.stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and "name" in row:
            out[str(row["name"])] = row
    if not out:
        pytest.fail(f"the check script printed no results.\n{proc.stdout}\n{proc.stderr}")
    return out


def test_the_script_reports_exactly_the_checks_this_file_knows_about(
    results: dict[str, dict[str, object]],
) -> None:
    assert set(results) == set(CHECKS)


@pytest.mark.parametrize("name", CHECKS)
def test_runtime_check(name: str, results: dict[str, dict[str, object]]) -> None:
    row = results.get(name)
    assert row is not None, f"{name} did not run"
    assert row["ok"], row["detail"]
