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
    "graph_structure_is_built_from_the_forms_parameters_parts_and_uses",
    "graph_renders_nodes_ports_and_wires_in_the_engines_styles",
    "graph_slider_and_panel_slider_stay_in_step_and_redraw",
    "selecting_a_part_highlights_its_node_wires_and_mesh",
    "graph_pans_zooms_drags_and_fits",
    "mount_draws_publishes_a_summary_and_redraws_on_a_parameter_change",
    "components_describe_each_part_its_readiness_and_the_functions_without_a_part",
    "components_a_guess_never_counts_as_a_real_size",
    "components_a_dummy_link_is_not_a_source",
    "components_card_renders_rows_and_missing_functions_and_is_absent_when_empty",
    "measure_a_standing_box_has_no_overhang_and_tips_at_45_degrees",
    "measure_a_block_hanging_off_a_small_base_would_tip_over",
    "measure_a_mushroom_overhang_counts_the_whole_underside_as_an_upper_bound",
    "measure_a_form_with_a_point_at_the_bottom_has_no_base_to_stand_on",
    "measure_overlapping_components_are_listed_with_their_volume_and_generated_parts_are_not",
    "measure_overhang_starts_at_45_degrees_from_straight_down",
    "measure_says_what_it_did_not_measure",
    "describe_shows_the_measurements_and_says_when_a_form_would_tip_over",
    "screening_rules_report_only_what_was_measured",
    "the_summary_offers_a_button_for_a_person_to_say_they_have_seen_the_form",
    "a_part_look_comes_from_its_own_process_material_finish_and_colour",
    "each_part_is_drawn_in_its_own_material",
    "a_part_with_no_make_block_keeps_the_product_look",
    "describe_sums_the_mass_from_each_parts_own_material",
    "a_behaviour_moves_its_parts_about_its_own_axis_and_origin",
    "a_person_drives_each_behaviour_in_the_studio",
    "a_component_part_reads_its_specs_from_its_part_record",
    "the_parts_list_and_the_3d_hover_show_a_components_specs",
    "the_next_step_is_the_most_important_open_decision_with_buttons",
    "the_running_cost_adds_materials_and_recorded_prices_and_says_what_it_leaves_out",
    "variants_can_be_tried_shown_side_by_side_and_picked_through_the_person",
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
