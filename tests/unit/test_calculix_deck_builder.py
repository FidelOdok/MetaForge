"""Unit tests for building a complete CalculiX static-stress deck (FORGE-234).

Live-validated separately (not in this unit suite -- no ccx/gmsh binaries
here): a real deck built by this exact code, run through the real ccx
solver on fidel-dev against a 100x20x10mm steel cantilever (fixed at
Surface1/x=0, 100N in -Z at Surface2/x=100, gmsh element_size=1.2mm),
matched Euler-Bernoulli beam theory (0.1mm tip deflection) within ~5% --
comfortably inside the ticket's own 10% acceptance bar. See the PR/Jira
comment for the full run.
"""

from __future__ import annotations

import pytest

from tool_registry.tools.calculix.deck_builder import build_static_stress_deck
from tool_registry.tools.calculix.inp_mesh import MeshData


def _cantilever_mesh() -> MeshData:
    """A minimal, hand-built cantilever-shaped mesh: two C3D4 tets forming a
    tiny box, a CPS3 surface elset at each end (the "faces" a real
    generate_mesh call would also emit), plus a T3D2 edge elset -- the same
    mixed-element shape gmsh actually produces, so the volume-only
    filtering logic is genuinely exercised."""
    mesh = MeshData()
    mesh.nodes = {
        1: (0.0, 0.0, 0.0),
        2: (0.0, 0.0, 10.0),
        3: (0.0, 20.0, 0.0),
        4: (0.0, 20.0, 10.0),
        5: (100.0, 0.0, 0.0),
        6: (100.0, 0.0, 10.0),
        7: (100.0, 20.0, 0.0),
        8: (100.0, 20.0, 10.0),
        9: (50.0, 10.0, 5.0),
        10: (50.0, 10.0, 6.0),  # an interior node only in the mesh, not on either face
    }
    mesh.elements = {
        101: ("T3D2", [1, 2]),
        201: ("CPS3", [1, 2, 3]),
        202: ("CPS3", [2, 4, 3]),
        203: ("CPS3", [5, 6, 7]),
        204: ("CPS3", [6, 8, 7]),
        301: ("C3D4", [1, 2, 3, 9]),
        302: ("C3D4", [5, 6, 7, 9]),
        303: ("C3D4", [3, 4, 9, 10]),
        304: ("C3D4", [6, 8, 9, 10]),
    }
    mesh.elsets = {
        "Line1": [101],
        "Surface1": [201, 202],  # x=0 face -- nodes 1,2,3,4
        "Surface2": [203, 204],  # x=100 face -- nodes 5,6,7,8
        "Volume1": [301, 302, 303, 304],
    }
    return mesh


class TestBuildStaticStressDeck:
    def test_deck_contains_the_expected_cards_in_order(self) -> None:
        mesh = _cantilever_mesh()
        deck = build_static_stress_deck(
            mesh,
            youngs_modulus_mpa=200000.0,
            poissons_ratio=0.30,
            fixed_node_set="Surface1",
            load_node_set="Surface2",
            load_force_n=(0.0, 0.0, -100.0),
        )
        for card in (
            "*NODE",
            "*ELEMENT, TYPE=C3D4, ELSET=Volume1",
            "*NSET, NSET=FIXED_Surface1",
            "*MATERIAL, NAME=MAT1",
            "*ELASTIC, TYPE=ISO",
            "*SOLID SECTION, ELSET=Volume1, MATERIAL=MAT1",
            "*STEP",
            "*STATIC",
            "*BOUNDARY",
            "FIXED_Surface1, 1, 3",
            "*CLOAD",
            "*NODE FILE",
            "U",
            "*EL FILE",
            "S",
            "*END STEP",
        ):
            assert card in deck, f"missing card: {card!r}"
        # cards appear in the order CalculiX expects (material before
        # section, section before step, boundary before load, end step last)
        assert deck.index("*ELASTIC") < deck.index("*SOLID SECTION")
        assert deck.index("*SOLID SECTION") < deck.index("*STEP")
        assert deck.index("*BOUNDARY") < deck.index("*CLOAD")
        assert deck.index("*CLOAD") < deck.index("*END STEP")

    def test_only_volume_elements_are_included_not_surface_or_edge(self) -> None:
        """The exact bug found live: leaving CPS3/T3D2 elements in a solved
        deck makes ccx fail ('gen3delem: first thickness ... is zero') --
        they're free face/edge markers gmsh emits, not meant to carry their
        own section stiffness."""
        mesh = _cantilever_mesh()
        deck = build_static_stress_deck(
            mesh,
            youngs_modulus_mpa=200000.0,
            poissons_ratio=0.30,
            fixed_node_set="Surface1",
            load_node_set="Surface2",
            load_force_n=(0.0, 0.0, -100.0),
        )
        assert "TYPE=CPS3" not in deck
        assert "TYPE=T3D2" not in deck
        # element 101 (T3D2) and 201-204 (CPS3) must not appear as element
        # definitions (their ids may legitimately appear as node ids, so
        # scan only inside *ELEMENT blocks, not the whole deck text).
        element_ids_present: set[int] = set()
        in_element_block = False
        for line in deck.splitlines():
            if line.startswith("*ELEMENT"):
                in_element_block = True
                continue
            if line.startswith("*"):
                in_element_block = False
                continue
            if in_element_block and line.strip():
                element_ids_present.add(int(line.split(",")[0]))
        assert element_ids_present == {301, 302, 303, 304}

    def test_load_force_is_divided_evenly_across_load_nodes(self) -> None:
        mesh = _cantilever_mesh()
        deck = build_static_stress_deck(
            mesh,
            youngs_modulus_mpa=200000.0,
            poissons_ratio=0.30,
            fixed_node_set="Surface1",
            load_node_set="Surface2",
            load_force_n=(0.0, 0.0, -100.0),
        )
        # Surface2's node set is {5, 6, 7, 8} -- 4 nodes, -100N total in Z.
        load_lines = deck.split("*CLOAD")[1].split("*NODE FILE")[0].strip().splitlines()
        parsed = [tuple(p.strip() for p in line.split(",")) for line in load_lines]
        assert parsed == [
            ("5", "3", "-25.0"),
            ("6", "3", "-25.0"),
            ("7", "3", "-25.0"),
            ("8", "3", "-25.0"),
        ]

    def test_zero_components_of_the_force_are_not_emitted(self) -> None:
        mesh = _cantilever_mesh()
        deck = build_static_stress_deck(
            mesh,
            youngs_modulus_mpa=200000.0,
            poissons_ratio=0.30,
            fixed_node_set="Surface1",
            load_node_set="Surface2",
            load_force_n=(10.0, 0.0, 0.0),
        )
        load_lines = deck.split("*CLOAD")[1].split("*NODE FILE")[0].strip().splitlines()
        # Only DOF 1 (X) lines, never DOF 2 or 3 with a zero magnitude.
        assert all(line.split(",")[1].strip() == "1" for line in load_lines if line.strip())

    def test_unknown_volume_elset_raises(self) -> None:
        mesh = _cantilever_mesh()
        with pytest.raises(ValueError, match="NotAVolumeSet"):
            build_static_stress_deck(
                mesh,
                youngs_modulus_mpa=200000.0,
                poissons_ratio=0.30,
                fixed_node_set="Surface1",
                load_node_set="Surface2",
                load_force_n=(0.0, 0.0, -100.0),
                volume_elset="NotAVolumeSet",
            )

    def test_unknown_fixed_node_set_raises(self) -> None:
        mesh = _cantilever_mesh()
        with pytest.raises(KeyError):
            build_static_stress_deck(
                mesh,
                youngs_modulus_mpa=200000.0,
                poissons_ratio=0.30,
                fixed_node_set="NotAFace",
                load_node_set="Surface2",
                load_force_n=(0.0, 0.0, -100.0),
            )

    def test_volume_elset_with_no_c3d_elements_raises(self) -> None:
        mesh = _cantilever_mesh()
        mesh.elsets["EmptyVolume"] = [201, 202]  # CPS3 only, no real volume elements
        with pytest.raises(ValueError, match="no volume"):
            build_static_stress_deck(
                mesh,
                youngs_modulus_mpa=200000.0,
                poissons_ratio=0.30,
                fixed_node_set="Surface1",
                load_node_set="Surface2",
                load_force_n=(0.0, 0.0, -100.0),
                volume_elset="EmptyVolume",
            )
