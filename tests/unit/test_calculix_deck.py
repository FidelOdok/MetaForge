"""Unit tests for CalculiX input-deck generation.

These assert the deck's *structure*, because CalculiX accepts a deck that is
structurally wrong in ways that produce no error: a set defined inside a step is
rejected outright, but a missing output request just yields an empty result
file, and a missing section card means elements with no stiffness. The
end-to-end proof that these decks actually solve lives in
``tests/integration/test_calculix_solver_fidelity.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tool_registry.tools.calculix.deck import (
    DeckBuilder,
    DeckError,
    LoadCase,
    Region,
    StepOptions,
    parse_load_cases,
)
from tool_registry.tools.calculix.materials import get_material
from tool_registry.tools.calculix.mesh import parse_inp_mesh

# A 1x1x1 mm cube of one C3D8 element -- enough to exercise every card.
CUBE_MESH = """\
*NODE, NSET=Nall
1, 0.0, 0.0, 0.0
2, 1.0, 0.0, 0.0
3, 1.0, 1.0, 0.0
4, 0.0, 1.0, 0.0
5, 0.0, 0.0, 1.0
6, 1.0, 0.0, 1.0
7, 1.0, 1.0, 1.0
8, 0.0, 1.0, 1.0
*ELEMENT, TYPE=C3D8, ELSET=Volume1
1, 1, 2, 3, 4, 5, 6, 7, 8
"""

STATIC_CASE = {
    "name": "tip_load",
    "constraints": [{"region": {"face": "zmin"}, "kind": "fixed"}],
    "point_loads": [{"region": {"face": "zmax"}, "fz": -100.0}],
}


@pytest.fixture()
def mesh(tmp_path: Path):
    path = tmp_path / "cube.inp"
    path.write_text(CUBE_MESH, encoding="utf-8")
    return parse_inp_mesh(path)


@pytest.fixture()
def builder(mesh):
    return DeckBuilder(mesh, get_material("al6061_t6"))


def card_index(deck: str, pattern: str) -> int:
    """Line index of the first card matching ``pattern``, or -1."""
    for index, line in enumerate(deck.splitlines()):
        if re.match(pattern, line.strip(), re.IGNORECASE):
            return index
    return -1


class TestStaticDeck:
    def test_includes_the_mesh_rather_than_copying_it(self, builder) -> None:
        deck = builder.build(LoadCase.model_validate(STATIC_CASE))
        assert "*INCLUDE, INPUT=cube.inp" in deck
        # The node coordinates themselves must not be duplicated into the deck.
        assert "1.0, 1.0, 1.0" not in deck

    def test_emits_material_and_section(self, builder) -> None:
        deck = builder.build(LoadCase.model_validate(STATIC_CASE))
        assert "*MATERIAL, NAME=Al6061-T6" in deck
        assert "*ELASTIC" in deck
        assert "*DENSITY" in deck
        assert "*SOLID SECTION, ELSET=Volume1, MATERIAL=Al6061-T6" in deck

    def test_material_values_are_in_consistent_units(self, builder) -> None:
        deck = builder.build(LoadCase.model_validate(STATIC_CASE))
        assert "68900, 0.33" in deck

    def test_emits_a_static_step(self, builder) -> None:
        deck = builder.build(LoadCase.model_validate(STATIC_CASE))
        assert "*STEP" in deck
        assert "*STATIC" in deck
        assert "*END STEP" in deck

    def test_emits_output_requests(self, builder) -> None:
        """Without these the .frd holds only the mesh and the solve looks empty."""
        deck = builder.build(LoadCase.model_validate(STATIC_CASE))
        assert "*NODE FILE" in deck
        assert "*EL FILE" in deck
        assert re.search(r"^S, E$", deck, re.MULTILINE)

    def test_sets_are_declared_before_the_step(self, builder) -> None:
        """CalculiX rejects a set defined inside a step."""
        deck = builder.build(LoadCase.model_validate(STATIC_CASE))
        step = card_index(deck, r"\*STEP")
        for index, line in enumerate(deck.splitlines()):
            if line.strip().upper().startswith(("*NSET", "*ELSET")):
                assert index < step, f"set declared inside the step: {line}"

    def test_constraint_fixes_three_dofs(self, builder) -> None:
        deck = builder.build(LoadCase.model_validate(STATIC_CASE))
        boundary = deck.split("*BOUNDARY")[1]
        assert re.search(r"MF_FIX1_\d+, 1, 1, 0\.0", boundary)
        assert re.search(r"MF_FIX1_\d+, 2, 2, 0\.0", boundary)
        assert re.search(r"MF_FIX1_\d+, 3, 3, 0\.0", boundary)

    def test_total_force_is_split_across_the_region(self, builder) -> None:
        """-100 N over the cube's 4 top nodes is -25 N each."""
        deck = builder.build(LoadCase.model_validate(STATIC_CASE))
        assert re.search(r"MF_LOAD1_\d+, 3, -25", deck)

    def test_undistributed_force_applies_in_full_at_each_node(self, builder) -> None:
        case = dict(STATIC_CASE)
        case["point_loads"] = [{"region": {"face": "zmax"}, "fz": -100.0, "distribute": False}]
        deck = builder.build(LoadCase.model_validate(case))
        assert re.search(r"MF_LOAD1_\d+, 3, -100", deck)

    def test_gravity_becomes_a_grav_dload(self, builder) -> None:
        case = {
            "name": "gravity",
            "constraints": [{"region": {"face": "zmin"}}],
            "gravity_mm_s2": [0.0, 0.0, -9810.0],
        }
        deck = builder.build(LoadCase.model_validate(case))
        assert "*DLOAD" in deck
        assert re.search(r"Volume1, GRAV, 9810, 0\.0, 0\.0, -1", deck)

    def test_pressure_references_an_element_set(self, builder) -> None:
        case = {
            "name": "pressure",
            "constraints": [{"region": {"face": "zmin"}}],
            "pressures": [{"element_set": "Volume1", "face_label": "P2", "magnitude_mpa": 2.5}],
        }
        deck = builder.build(LoadCase.model_validate(case))
        assert "Volume1, P2, 2.5" in deck

    def test_nlgeom_is_opt_in(self, builder) -> None:
        case = LoadCase.model_validate(STATIC_CASE)
        assert "NLGEOM" not in builder.build(case)
        assert "*STEP, NLGEOM" in builder.build(case, StepOptions(nlgeom=True))

    def test_named_node_set_is_used_directly(self, builder) -> None:
        case = {
            "name": "by_set",
            "constraints": [{"region": {"node_set": "Nall"}}],
            "point_loads": [{"region": {"face": "zmax"}, "fz": -10.0}],
        }
        assert "*BOUNDARY" in builder.build(LoadCase.model_validate(case))

    def test_builder_is_reusable_across_cases(self, builder) -> None:
        """Set counters must reset, or a second deck accumulates the first's sets."""
        first = builder.build(LoadCase.model_validate(STATIC_CASE))
        second = builder.build(LoadCase.model_validate(STATIC_CASE))
        assert first == second


class TestModalDeck:
    def test_modal_emits_frequency_and_no_loads(self, builder) -> None:
        case = {"name": "modes", "constraints": [{"region": {"face": "zmin"}}]}
        deck = builder.build(LoadCase.model_validate(case), StepOptions(analysis="modal"))
        assert "*FREQUENCY" in deck
        assert "*CLOAD" not in deck
        assert "*STATIC" not in deck

    def test_eigenmode_count_is_written(self, builder) -> None:
        case = {"name": "modes", "constraints": [{"region": {"face": "zmin"}}]}
        deck = builder.build(
            LoadCase.model_validate(case), StepOptions(analysis="modal", eigenmodes=7)
        )
        assert re.search(r"\*FREQUENCY\n7", deck)

    def test_modal_needs_no_mechanical_load(self, builder) -> None:
        case = {"name": "modes", "constraints": [{"region": {"face": "zmin"}}]}
        builder.build(LoadCase.model_validate(case), StepOptions(analysis="modal"))


class TestThermalDeck:
    @pytest.fixture()
    def thermal_case(self) -> dict:
        return {
            "name": "hot",
            "thermal_boundaries": [{"region": {"face": "zmin"}, "temperature_k": 350.0}],
            "heat_fluxes": [{"region": {"face": "zmax"}, "power_mw": 500.0}],
        }

    def test_emits_heat_transfer_step(self, builder, thermal_case) -> None:
        deck = builder.build(LoadCase.model_validate(thermal_case), StepOptions(analysis="thermal"))
        assert "*HEAT TRANSFER, STEADY STATE" in deck

    def test_transient_omits_the_steady_state_flag(self, builder, thermal_case) -> None:
        deck = builder.build(
            LoadCase.model_validate(thermal_case),
            StepOptions(analysis="thermal", steady_state=False),
        )
        assert "*HEAT TRANSFER\n" in deck
        assert "STEADY STATE" not in deck

    def test_emits_thermal_material_properties(self, builder, thermal_case) -> None:
        deck = builder.build(LoadCase.model_validate(thermal_case), StepOptions(analysis="thermal"))
        assert "*CONDUCTIVITY" in deck
        assert "*SPECIFIC HEAT" in deck
        assert "*EXPANSION, ZERO=293.15" in deck

    def test_temperature_uses_dof_eleven(self, builder, thermal_case) -> None:
        deck = builder.build(LoadCase.model_validate(thermal_case), StepOptions(analysis="thermal"))
        assert re.search(r"MF_TEMP1_\d+, 11, 11, 350", deck)

    def test_heat_flux_is_distributed(self, builder, thermal_case) -> None:
        """500 mW over 4 nodes is 125 mW each."""
        deck = builder.build(LoadCase.model_validate(thermal_case), StepOptions(analysis="thermal"))
        assert re.search(r"MF_FLUX1_\d+, 11, 125", deck)

    def test_initial_conditions_are_emitted(self, builder, thermal_case) -> None:
        deck = builder.build(LoadCase.model_validate(thermal_case), StepOptions(analysis="thermal"))
        assert "*INITIAL CONDITIONS, TYPE=TEMPERATURE" in deck

    def test_convection_becomes_a_film_card(self, builder) -> None:
        case = {
            "name": "cooled",
            "convections": [
                {
                    "element_set": "Volume1",
                    "face_label": "F3",
                    "film_coefficient": 0.025,
                    "sink_temperature_k": 293.15,
                }
            ],
        }
        deck = builder.build(LoadCase.model_validate(case), StepOptions(analysis="thermal"))
        assert "*FILM" in deck
        assert "Volume1, F3, 293.15, 0.025" in deck

    def test_thermal_output_requests_temperature(self, builder, thermal_case) -> None:
        deck = builder.build(LoadCase.model_validate(thermal_case), StepOptions(analysis="thermal"))
        assert re.search(r"\*NODE FILE\nNT", deck)


class TestValidation:
    def test_static_without_constraints_is_rejected(self, builder) -> None:
        """An unrestrained model gives a singular stiffness matrix."""
        case = {"name": "floating", "point_loads": [{"region": {"face": "zmax"}, "fz": -1.0}]}
        with pytest.raises(DeckError, match="no displacement constraints"):
            builder.build(LoadCase.model_validate(case))

    def test_static_without_loads_is_rejected(self, builder) -> None:
        case = {"name": "unloaded", "constraints": [{"region": {"face": "zmin"}}]}
        with pytest.raises(DeckError, match="no mechanical load"):
            builder.build(LoadCase.model_validate(case))

    def test_thermal_without_boundary_conditions_is_rejected(self, builder) -> None:
        case = {"name": "cold", "constraints": [{"region": {"face": "zmin"}}]}
        with pytest.raises(DeckError, match="no thermal boundary conditions"):
            builder.build(LoadCase.model_validate(case), StepOptions(analysis="thermal"))

    def test_unknown_node_set_names_the_available_ones(self, builder) -> None:
        case = {
            "name": "bad_set",
            "constraints": [{"region": {"node_set": "Missing"}}],
            "point_loads": [{"region": {"face": "zmax"}, "fz": -1.0}],
        }
        with pytest.raises(DeckError, match="Available sets: Nall"):
            builder.build(LoadCase.model_validate(case))

    def test_unknown_element_set_is_rejected(self, builder) -> None:
        case = {
            "name": "bad_elset",
            "constraints": [{"region": {"face": "zmin"}}],
            "pressures": [{"element_set": "Missing", "magnitude_mpa": 1.0}],
        }
        with pytest.raises(DeckError, match="Element set 'Missing' not found"):
            builder.build(LoadCase.model_validate(case))

    def test_empty_named_set_is_rejected(self, mesh, builder) -> None:
        """A selection that catches nothing would silently do nothing.

        A bounding-box face always contains at least one node, so the only way
        to select nothing is an empty named set -- which a mesher can emit.
        """
        mesh.node_sets["Empty"] = []
        case = {
            "name": "empty",
            "constraints": [{"region": {"node_set": "Empty"}}],
            "point_loads": [{"region": {"face": "zmax"}, "fz": -1.0}],
        }
        with pytest.raises(DeckError, match="selected no nodes"):
            builder.build(LoadCase.model_validate(case))

    def test_unknown_constraint_kind_is_rejected(self, builder) -> None:
        case = {
            "name": "bad_kind",
            "constraints": [{"region": {"face": "zmin"}, "kind": "welded"}],
            "point_loads": [{"region": {"face": "zmax"}, "fz": -1.0}],
        }
        with pytest.raises(DeckError, match="Unknown constraint kind"):
            builder.build(LoadCase.model_validate(case))

    def test_out_of_range_dof_is_rejected(self, builder) -> None:
        case = {
            "name": "bad_dof",
            "constraints": [{"region": {"face": "zmin"}, "dofs": [7]}],
            "point_loads": [{"region": {"face": "zmax"}, "fz": -1.0}],
        }
        with pytest.raises(DeckError, match="degree of freedom"):
            builder.build(LoadCase.model_validate(case))

    def test_gravity_vector_must_have_three_components(self, builder) -> None:
        case = {
            "name": "bad_gravity",
            "constraints": [{"region": {"face": "zmin"}}],
            "gravity_mm_s2": [0.0, -9810.0],
        }
        with pytest.raises(DeckError, match="exactly 3 components"):
            builder.build(LoadCase.model_validate(case))


class TestRegion:
    def test_region_needs_exactly_one_selector(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            Region()
        with pytest.raises(ValueError, match="exactly one"):
            Region(node_set="Nall", face="zmin")

    def test_roller_constraint_fixes_one_dof(self) -> None:
        from tool_registry.tools.calculix.deck import Constraint

        constraint = Constraint(region=Region(face="zmin"), kind="roller_z")
        assert constraint.resolved_dofs() == (3,)

    def test_explicit_dofs_override_kind(self) -> None:
        from tool_registry.tools.calculix.deck import Constraint

        constraint = Constraint(region=Region(face="zmin"), kind="fixed", dofs=[2])
        assert constraint.resolved_dofs() == (2,)


class TestParseLoadCases:
    def test_none_yields_no_cases(self) -> None:
        assert parse_load_cases(None) == []

    def test_single_dict_is_wrapped(self) -> None:
        cases = parse_load_cases(STATIC_CASE)
        assert len(cases) == 1
        assert cases[0].name == "tip_load"

    def test_list_is_preserved_in_order(self) -> None:
        cases = parse_load_cases([STATIC_CASE, {**STATIC_CASE, "name": "second"}])
        assert [c.name for c in cases] == ["tip_load", "second"]

    def test_missing_names_are_generated_uniquely(self) -> None:
        payload = {k: v for k, v in STATIC_CASE.items() if k != "name"}
        cases = parse_load_cases([payload, dict(payload)], default_name="run")
        assert [c.name for c in cases] == ["run_1", "run_2"]

    def test_bare_string_is_rejected_with_an_example(self) -> None:
        """A name carries no physics; solving it silently would report zero stress."""
        with pytest.raises(DeckError) as excinfo:
            parse_load_cases("gravity_1g")
        assert "constraints" in str(excinfo.value)
        assert "point_loads" in str(excinfo.value)

    def test_non_object_entry_is_rejected(self) -> None:
        with pytest.raises(DeckError, match="must be an object"):
            parse_load_cases([42])

    def test_invalid_case_names_itself_in_the_error(self) -> None:
        with pytest.raises(DeckError, match="broken"):
            parse_load_cases([{"name": "broken", "constraints": [{"region": {}}]}])


class TestWrite:
    def test_write_creates_the_deck_file(self, builder, tmp_path: Path) -> None:
        path = builder.write(LoadCase.model_validate(STATIC_CASE), tmp_path / "out" / "case.inp")
        assert path.exists()
        assert "*STEP" in path.read_text(encoding="utf-8")
