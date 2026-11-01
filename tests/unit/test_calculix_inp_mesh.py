"""Unit tests for parsing a gmsh-written CalculiX .inp mesh (FORGE-234).

The fixture below is a small, hand-verified excerpt matching real gmsh 4.x
output (confirmed live against fidel-dev: a 100x20x10mm box meshed from a
STEP file) -- a *NODE block, several *ELEMENT blocks (T3D2 edges, CPS3
surface triangles, C3D4 volume tets), each in its own named ELSET, exactly
the shape freecad.generate_mesh actually produces.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tool_registry.tools.calculix.inp_mesh import parse_mesh_inp

_SAMPLE_INP = """\
*Heading
 /workspace/box.inp
*NODE
1, 0, 0, 0
2, 0, 0, 10
3, 0, 20, 0
4, 0, 20, 10
5, 100, 0, 0
6, 100, 0, 10
7, 100, 20, 0
8, 100, 20, 10
9, 50, 10, 5
******* E L E M E N T S *************
*ELEMENT, type=T3D2, ELSET=Line1
101, 1, 2
*ELEMENT, type=CPS3, ELSET=Surface1
201, 1, 2, 3
202, 2, 4, 3
*ELEMENT, type=CPS3, ELSET=Surface2
203, 5, 6, 7
204, 6, 8, 7
*ELEMENT, type=C3D4, ELSET=Volume1
301, 1, 2, 3, 9
302, 5, 6, 7, 9
"""


@pytest.fixture
def sample_inp_path(tmp_path: Path) -> str:
    path = tmp_path / "box.inp"
    path.write_text(_SAMPLE_INP, encoding="utf-8")
    return str(path)


class TestParseMeshInp:
    def test_parses_all_nodes(self, sample_inp_path: str) -> None:
        mesh = parse_mesh_inp(sample_inp_path)
        assert len(mesh.nodes) == 9
        assert mesh.nodes[1] == (0.0, 0.0, 0.0)
        assert mesh.nodes[5] == (100.0, 0.0, 0.0)

    def test_parses_all_elements_with_their_type(self, sample_inp_path: str) -> None:
        mesh = parse_mesh_inp(sample_inp_path)
        assert mesh.elements[101] == ("T3D2", [1, 2])
        assert mesh.elements[201] == ("CPS3", [1, 2, 3])
        assert mesh.elements[301] == ("C3D4", [1, 2, 3, 9])

    def test_groups_elements_into_named_elsets(self, sample_inp_path: str) -> None:
        mesh = parse_mesh_inp(sample_inp_path)
        assert mesh.elsets["Line1"] == [101]
        assert mesh.elsets["Surface1"] == [201, 202]
        assert mesh.elsets["Surface2"] == [203, 204]
        assert mesh.elsets["Volume1"] == [301, 302]

    def test_the_elements_banner_line_does_not_corrupt_parsing(self, sample_inp_path: str) -> None:
        """The '******* E L E M E N T S *************' line starts with '*'
        but names no real section -- must reset to "no active section",
        never be mistaken for a NODE or ELEMENT block itself."""
        mesh = parse_mesh_inp(sample_inp_path)
        # If the banner leaked through, node 9 would have been misread as
        # more node/element data instead of the real *ELEMENT that follows.
        assert 101 in mesh.elements


class TestNodeIdsForElset:
    def test_union_of_element_nodes_deduplicated_and_sorted(self, sample_inp_path: str) -> None:
        mesh = parse_mesh_inp(sample_inp_path)
        # Surface1's two triangles share nodes 2 and 3.
        assert mesh.node_ids_for_elset("Surface1") == [1, 2, 3, 4]

    def test_unknown_elset_raises_with_available_names_listed(self, sample_inp_path: str) -> None:
        mesh = parse_mesh_inp(sample_inp_path)
        with pytest.raises(KeyError, match="Volume1"):
            mesh.node_ids_for_elset("NotARealSet")


class TestBoundingBoxForNodes:
    def test_computes_the_bounding_box(self, sample_inp_path: str) -> None:
        mesh = parse_mesh_inp(sample_inp_path)
        bbox = mesh.bounding_box_for_nodes(mesh.node_ids_for_elset("Surface1"))
        assert bbox == {
            "min_x": 0.0,
            "max_x": 0.0,
            "min_y": 0.0,
            "max_y": 20.0,
            "min_z": 0.0,
            "max_z": 10.0,
        }

    def test_empty_node_list_raises(self, sample_inp_path: str) -> None:
        mesh = parse_mesh_inp(sample_inp_path)
        with pytest.raises(ValueError, match="empty"):
            mesh.bounding_box_for_nodes([])


class TestFaceSummaries:
    def test_reports_only_surface_elsets_by_geometry(self, sample_inp_path: str) -> None:
        """FORGE-234: a caller must be able to tell Surface1 (x=0, the fixed
        end) apart from Surface2 (x=100, the free end) by bounding box
        alone -- the name is otherwise opaque."""
        mesh = parse_mesh_inp(sample_inp_path)
        summaries = mesh.face_summaries()
        assert set(summaries) == {"Surface1", "Surface2"}  # Line1/Volume1 excluded
        assert summaries["Surface1"]["bounding_box"]["max_x"] == 0.0
        assert summaries["Surface2"]["bounding_box"]["min_x"] == 100.0
